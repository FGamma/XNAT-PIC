import hashlib
import socket
import uuid
from pathlib import Path

from pydicom import dcmread
from pydicom.dataset import FileMetaDataset, FileDataset
from pydicom.errors import InvalidDicomError
from pydicom.uid import generate_uid, UID, ExplicitVRLittleEndian, SecondaryCaptureImageStorage, \
    PYDICOM_IMPLEMENTATION_UID

from enums.dicom_modality import DicomModality


class DicomCompatibilityService:
    @staticmethod
    def update_study_instance_uid_map(dicom_file: Path, study_instance_uid_map: dict[str, str]) -> dict[str, str]:
        sub_exp = DicomCompatibilityService._get_sub_exp(dicom_file)

        if sub_exp not in study_instance_uid_map:
            study_instance_uid_map[sub_exp] = generate_uid()

        return study_instance_uid_map

    @staticmethod
    def get_compatible_dicom_file(dicom_file: Path, exp_uid_map: dict[str, str]):
        try:
            ds = dcmread(dicom_file)
        except InvalidDicomError as e:
            raise RuntimeError(f"Invalid DICOM file: {dicom_file}") from e
        except OSError as e:
            raise RuntimeError(f"Unable to read DICOM file: {dicom_file}") from e

        try:
            ds.file_meta = DicomCompatibilityService._build_meta_file(ds)
        except Exception as e:
            raise RuntimeError("Error creating file meta information") from e

        try:
            manufacturer = str(getattr(ds, "Manufacturer", "")).strip()
            implementation_version_name = str(
                getattr(ds.file_meta, "ImplementationVersionName", "")
            ).strip()

            if manufacturer.lower() == "fujifilm visualsonics, inc." or implementation_version_name.lower() == "living image":
                return DicomCompatibilityService._compatible_ivis_fujifilm(ds, dicom_file, exp_uid_map)

        except Exception as e:
            raise RuntimeError("Error creating dicom file") from e

    @staticmethod
    def _build_meta_file(ds: FileDataset) -> FileMetaDataset:
        # File Meta Dataset
        if not hasattr(ds, "file_meta") or ds.file_meta is None:
            ds.file_meta = FileMetaDataset()

        # File Meta Information Version
        if (not hasattr(ds.file_meta, 'FileMetaInformationVersion') or
                ds.file_meta.FileMetaInformationVersion is None):
            ds.file_meta.FileMetaInformationVersion = b"\x00\x01"

        # Media Storage SOP Class UID
        ds.file_meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage

        # Media Storage SOP Instance UID
        if hasattr(ds.file_meta, "MediaStorageSOPInstanceUID") and \
                UID(ds.file_meta.MediaStorageSOPInstanceUID).is_valid:
            pass
        elif hasattr(ds, "SOPInstanceUID") and UID(ds.SOPInstanceUID).is_valid:
            ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
        else:
            new_uid = generate_uid()
            ds.file_meta.MediaStorageSOPInstanceUID = new_uid

        # Transfer Syntax UID
        if not hasattr(ds.file_meta, "TransferSyntaxUID") or \
                not UID(ds.file_meta.TransferSyntaxUID).is_valid:
            ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

        # Implementation Class UID
        if not hasattr(ds.file_meta, "ImplementationClassUID") or \
                not UID(ds.file_meta.ImplementationClassUID).is_valid:
            ds.file_meta.ImplementationClassUID = DicomCompatibilityService._generate_implementation_uid() # ID of the system which created the file or PYDICOM_IMPLEMENTATION_UID

        return ds.file_meta

    @staticmethod
    def _compatible_ivis_fujifilm(ds: FileDataset, dicom_file: Path, exp_uid_map: dict[str, str]) -> list[tuple[FileDataset, str]]:
        ds.SOPClassUID = ds.file_meta.MediaStorageSOPClassUID
        ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID

        sub_exp = DicomCompatibilityService._get_sub_exp(dicom_file)
        study_instance_uid = exp_uid_map.get(sub_exp)
        if not study_instance_uid:
            raise KeyError(f"Missing StudyInstanceUID mapping for key '{sub_exp}'")
        ds.PatientID = sub_exp
        ds.StudyInstanceUID = study_instance_uid
        ds.SeriesInstanceUID = generate_uid()
        ds.ImageType = ["DERIVED", "SECONDARY", "OTHER"]
        if not hasattr(ds, "Modality"):
            ds.Modality = DicomModality.OT.value

        filename = f"{dicom_file.stem}.dcm"
        new_file = ds
        return [(new_file, filename)]

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
    def _get_sub_exp(dicom_file: Path) -> str:
        try:
            return dicom_file.parents[2].name + "_" + dicom_file.parents[1].name
        except IndexError as e:
            raise ValueError(
                f"Unexpected dicom path structure for file: {dicom_file}"
            ) from e