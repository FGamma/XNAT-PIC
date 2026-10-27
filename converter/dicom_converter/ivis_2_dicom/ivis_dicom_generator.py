import hashlib
import re
import socket
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image
from pydicom.dataset import FileMetaDataset, FileDataset
from pydicom.uid import SecondaryCaptureImageStorage, generate_uid, \
    ExplicitVRLittleEndian, PYDICOM_IMPLEMENTATION_UID, UID

from converter.dicom_converter.ivis_2_dicom.ivis_dataclasses.ivis_image_info import IvisImageInfo
from converter.dicom_converter.ivis_2_dicom.ivis_metadata_parser import \
    IvisMetadata
from enums.converter_level import ConverterLevel
from enums.dicom_modality import DicomModality


class IvisDicomGenerator:
    def __init__(self, metadata_parse: IvisMetadata):
        self._metadata_parse = metadata_parse

    def generate_dicom(self, dst: Path, level: ConverterLevel) -> tuple[int, list[tuple[Path, Exception]]]:
        total_scans_tmp = 0
        failed_scans_tmp = []
        image_list = self._metadata_parse.get_images()
        study_instance_uid = generate_uid()

        section_tags = self._extract_section_tags()

        for ind, image in enumerate(image_list):
            try:
                if self._is_valid_scan(image.file_path):
                    dst_name = self._sanitize_path_component(dst.name)
                    image_name = self._sanitize_path_component(Path(image.filename).stem)

                    if level == ConverterLevel.EXPERIMENT:
                        dst_tmp = dst / image_name
                    else:
                        dst_tmp = dst.parent.parent / dst_name / image_name

                    total_scans_tmp += 1

                    file_meta = self._build_meta_file()
                    ds = self._build_dataset(file_meta, study_instance_uid)

                    ds = self._set_patient_info(dst_tmp, ds)

                    img = self._load_image(image.file_path)
                    ds = self._set_photometric_interpretation(img, ds)
                    ds = self._set_bits_allocated(img, ds)
                    ds = self._set_windowing_configuration(img, ds)
                    ds.PixelData = img.tobytes()

                    ds = self._set_section_tag(section_tags, ds)

                    if image.metadata:
                        # Private tags for IVIS
                        ds.add_new((0x0011, 0x0010), "LO", "IVIS_PERKINELMER")
                        ds = self._set_image_tag(image, ds)

                    out_path = dst_tmp / Path(image.filename).with_suffix(".dcm")
                    self._write_dicom(out_path, ds)

            except Exception as exc:
                failed_scans_tmp.append((image.file_path, exc))
                continue

        return total_scans_tmp, failed_scans_tmp

    def _extract_section_tags(self) -> dict:
        tags = {}

        sections_list = self._metadata_parse.get_sections()

        for section in sections_list:
            if section.name == "ClickNumber":
                tags["SoftwareVersions"] = self._stringify(
                    section.get_metadata_value("Living Image Version")
                )

                value = self._stringify(
                    section.get_metadata_value("ClickNumber Index")
                )
                if value:
                    tags["InstanceNumber"] = int(value)

            elif section.name == "User Label Name Set":
                value = self._stringify(
                    section.get_metadata_value("Animal Number")
                )
                if value:
                    tags["PatientID"] = value

                tags["PatientSex"] = self._stringify(
                    section.get_metadata_value("Sex")
                )
                tags["PatientSpeciesDescription"] = self._stringify(
                    section.get_metadata_value("Animal Model")
                )
                tags["StrainDescription"] = self._stringify(
                    section.get_metadata_value("Animal Strain")
                )
                tags["StudyID"] = self._stringify(
                    section.get_metadata_value("Experiment")
                )

            elif section.name == "User Input":
                tags["OperatorsName"] = self._stringify(
                    section.get_metadata_value("User ID")
                )
                tags["InstitutionalDepartmentName"] = self._stringify(
                    section.get_metadata_value("Group")
                )

            elif section.name == "Analysis User Input":
                pass

            elif section.name == "Camera System Info":
                tags["DeviceSerialNumber"] = self._stringify(
                    section.get_metadata_value("Camera System ID")
                )
                tags["StationName"] = self._stringify(
                    section.get_metadata_value("Camera System alias")
                )
                tags["ManufacturerModelName"] = self._stringify(
                    section.get_metadata_value("System Configuration")
                )
                tags["Manufacturer"] = "PerkinElmer"

                tags["DetectorType"] = self._normalize_cs(
                    section.get_metadata_value("Camera Type")
                )
                tags["DetectorID"] = self._stringify(
                    section.get_metadata_value("Camera CCD Type")
                )

            elif section.name == "Graph Controls":
                value = self._stringify(
                    section.get_metadata_value("Calibration Value Per Count")
                )
                if value:
                    tags["RescaleSlope"] = float(value)

                tags["RescaleIntercept"] = 0.0
                tags["RescaleType"] = self._stringify(
                    section.get_metadata_value("Intensity Calibration")
                )

                reversed_value = self._stringify(
                    section.get_metadata_value("Reversed")
                )
                tags["PixelIntensityRelationship"] = (
                    "INVERSE" if reversed_value == "1" else "LIN"
                )

                value = self._stringify(
                    section.get_metadata_value("Rotation")
                )
                if value:
                    tags["ImageRotation"] = int(value)

        return tags

    def _set_image_tag(self, image: IvisImageInfo, ds: FileDataset) -> FileDataset:
        ds.SeriesDescription = image.section

        ds = self._set_acquisition_datetime(image, ds)

        # -------- COMMON ----------
        value = self._stringify(image.get_metadata_value("Binning Factor"))
        if value:
            ds.add_new((0x0011, 0x1001), "US", int(value))

        if "photo" in image.section:
            value = self._stringify(image.get_metadata_value("f Number"))
            if value:
                ds.add_new((0x0011, 0x1002), "US", int(value))

            ds.add_new((0x0011, 0x1003), "DS",
                       self._stringify(
                           image.get_metadata_value("Field of View")))

            value = self._stringify(image.get_metadata_value("Filter Position"))
            if value:
                ds.add_new((0x0011, 0x1004), "US", int(value))

            ds.add_new((0x0011, 0x1005), "LO",
                       self._stringify(
                           image.get_metadata_value("Emission filter")))

            ds.add_new((0x0011, 0x1006), "DS",
                       self._stringify(
                           image.get_metadata_value("Exposure Time Sec")))

        elif "readbias" in image.section:
            ds.add_new((0x0011, 0x1007), "DS",
                       self._stringify(
                           image.get_metadata_value("Read Bias Level")))

            ds.add_new((0x0011, 0x1008), "DS",
                       self._stringify(
                           image.get_metadata_value("Demand Temperature")))

            ds.add_new((0x0011, 0x1009), "DS",
                       self._stringify(
                           image.get_metadata_value("Measured Temperature")))

            value = self._stringify(image.get_metadata_value("Data Multiplier"))
            if value:
                ds.add_new((0x0011, 0x1010), "US", int(value))

            ds.add_new((0x0011, 0x1011), "DS",
                       self._stringify(
                           image.get_metadata_value("Background Exposure (Seconds)")))

        elif "luminescent" in image.section:
            ds.add_new((0x0011, 0x1012), "DS",
                       self._stringify(
                           image.get_metadata_value("Luminescent Exposure (Seconds)")))

            ds.add_new((0x0011, 0x1013), "LO",
                       self._stringify(
                           image.get_metadata_value("Luminescent Exposure Units")))

            value = self._stringify(image.get_metadata_value("f Number"))
            if value:
                ds.add_new((0x0011, 0x1002), "US", int(value))

            ds.add_new((0x0011, 0x1003), "DS",
                       self._stringify(
                           image.get_metadata_value("Field of View")))

            ds.add_new((0x0011, 0x1007), "DS",
                       self._stringify(
                           image.get_metadata_value("Read Bias Level")))

            ds.add_new((0x0011, 0x1005), "LO",
                       self._stringify(
                           image.get_metadata_value("Emission filter")))

            value = self._stringify(image.get_metadata_value("Filter Position"))
            if value:
                ds.add_new((0x0011, 0x1004), "US", int(value))

            ds.add_new((0x0011, 0x1014), "LO",
                       self._stringify(
                           image.get_metadata_value("Excitation filter")))

            ds.add_new((0x0011, 0x1008), "DS",
                       self._stringify(
                           image.get_metadata_value("Demand Temperature")))

            ds.add_new((0x0011, 0x1009), "DS",
                       self._stringify(
                           image.get_metadata_value("Measured Temperature")))

        return ds

    @staticmethod
    def _is_valid_scan(path: Path) -> bool:
        return path.is_file()

    @staticmethod
    def _build_meta_file() -> FileMetaDataset:
        # --- File Meta Dataset ---
        file_meta = FileMetaDataset()

        # --- File Meta Information Version ---
        file_meta.FileMetaInformationVersion = b"\x00\x01"

        # --- Media Storage SOP Class UID ---
        file_meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage

        # --- Media Storage SOP Instance UID ---
        new_uid = generate_uid()
        file_meta.MediaStorageSOPInstanceUID = new_uid

        # --- Transfer Syntax UID ---
        file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

        # --- Implementation Class UID ---
        file_meta.ImplementationClassUID = IvisDicomGenerator._generate_implementation_uid()

        return file_meta

    @staticmethod
    def _build_dataset(file_meta: FileMetaDataset, study_instance_uid: UID) -> FileDataset:
        ds = FileDataset(filename_or_obj="",
                         dataset={},
                         file_meta=file_meta,
                         preamble=b"\0" * 128)

        now = datetime.now()
        ds.InstanceCreationDate = now.strftime('%Y%m%d')
        ds.InstanceCreationTime = now.strftime('%H%M%S')
        ds.SOPClassUID = file_meta.get("MediaStorageSOPClassUID")
        ds.SOPInstanceUID = file_meta.get("MediaStorageSOPInstanceUID")
        ds.Modality = DicomModality.OI.value
        ds.ImageType = ["DERIVED", "SECONDARY", "OTHER"]
        ds.StudyInstanceUID = study_instance_uid
        ds.SeriesInstanceUID = generate_uid()
        ds.SeriesNumber = 1

        return ds

    @staticmethod
    def _set_patient_info(dst_tmp: Path, ds: FileDataset) -> FileDataset:
        ds.PatientName = dst_tmp.parts[-2]
        ds.PatientID = dst_tmp.parts[-2]

        return ds

    @staticmethod
    def _load_image(path: Path) -> np.ndarray:
        img = Image.open(path)
        np_img = np.array(img)

        return np_img

    @staticmethod
    def _set_photometric_interpretation(img: np.ndarray, ds: FileDataset) -> FileDataset:
        if img.ndim == 2:
            rows, cols = img.shape
            ds.SamplesPerPixel = 1
            ds.PhotometricInterpretation = "MONOCHROME2"

        elif img.ndim == 3 and img.shape[2] == 3:
            rows, cols, _ = img.shape
            ds.SamplesPerPixel = 3
            ds.PhotometricInterpretation = "RGB"
            ds.PlanarConfiguration = 0  # RGBRGBRGB

        else:
            raise ValueError("Unsupported image format. Supported formats: MONOCHROME2 and RGB")

        ds.Rows = rows
        ds.Columns = cols

        return ds

    @staticmethod
    def _set_bits_allocated(img: np.ndarray, ds: FileDataset) -> FileDataset:
        if img.dtype == np.uint8:
            ds.BitsAllocated = 8
            ds.BitsStored = 8
            ds.HighBit = 7
            ds.PixelRepresentation = 0
        elif img.dtype == np.uint16:
            ds.BitsAllocated = 16
            ds.BitsStored = 16
            ds.HighBit = 15
            ds.PixelRepresentation = 0
        else:
            raise ValueError(
                f"Unsupported pixel data type: {img.dtype}. "
                "Supported types are uint8 and uint16."
            )

        return ds

    @staticmethod
    def _set_windowing_configuration(img: np.ndarray, ds: FileDataset) -> FileDataset:
        vmin = int(img.min())
        vmax = int(img.max())
        ds.SmallestImagePixelValue = vmin
        ds.LargestImagePixelValue = vmax

        if vmin == vmax:
            window_center = vmin
            window_width = 1
        else:
            window_center = int((vmin + vmax) / 2.0)
            window_width = int((vmax - vmin))

        ds.WindowCenter = str(window_center)
        ds.WindowWidth = str(window_width)

        return ds

    @staticmethod
    def _set_section_tag(section_tags: dict, ds: FileDataset) -> FileDataset:
        for tag, value in section_tags.items():
            setattr(ds, tag, value)

        return ds

    @staticmethod
    def _write_dicom(out_path: Path, ds: FileDataset):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        ds.is_little_endian = True
        ds.is_implicit_VR = False
        ds.save_as(out_path, write_like_original=False)
        print(f"[OK] Saved: {out_path}")

    @staticmethod
    def _generate_implementation_uid() -> UID:
        try:
            # MAC address of computer
            mac_address = uuid.getnode()

            # Hostname of computer
            hostname = socket.gethostname()

            # Combination of data in a string
            unique_string = f"{mac_address}-{hostname}"

            # One-way hash to avoid exposing system information
            hash_object = hashlib.sha256(unique_string.encode())
            hex_dig = hash_object.hexdigest()

            numeric_hash = int(hex_dig, 16)
            numeric_hash_str = str(numeric_hash)

            # Keep only 64 digits
            implementation_uid = numeric_hash_str[:64]

            uid = UID(implementation_uid)

            # Validate UID
            if not uid.is_valid:
                raise ValueError("Generated ImplementationClassUID is invalid")

            return uid

        except Exception:
            return PYDICOM_IMPLEMENTATION_UID

    @staticmethod
    def _sanitize_path_component(value: str) -> str:
        value = re.sub(r'[.<>:"/\\|?*\x00-\x1f]', "_", value.strip())
        value = value.rstrip(" .")

        if not value:
            raise ValueError("Invalid path component.")

        return value

    @staticmethod
    def _stringify(value: str | None) -> str | None:
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def _normalize_cs(value: str | None) -> str:
        if value is None:
            return ""
        text = str(value).strip().upper()
        text = re.sub(r"[^A-Z0-9 _]", "_", text)
        return text[:16]

    @staticmethod
    def _set_acquisition_datetime (image: IvisImageInfo, ds: FileDataset) -> FileDataset:
        raw_date = image.get_metadata_value("Acquisition Date")
        raw_time = image.get_metadata_value("Acquisition Time")

        try:
            if not raw_date or not raw_time:
                raise ValueError("Acquisition date or time missing")

            dt = datetime.strptime(
                f"{str(raw_date).strip()} {str(raw_time).strip()}",
                "%A, %B %d, %Y %H:%M:%S"
            )

        except ValueError:
            dt = datetime.now()

        ds.StudyDate = dt.strftime("%Y%m%d")
        ds.AcquisitionDate = dt.strftime("%Y%m%d")
        ds.AcquisitionTime = dt.strftime("%H%M%S")

        return ds