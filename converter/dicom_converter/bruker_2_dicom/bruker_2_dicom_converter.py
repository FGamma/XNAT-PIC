# -*- coding: utf-8 -*-
"""
Created on Dec 7 2021

@author: Riccardo Gambino

"""
import datetime
import os
import re
from glob import glob
import shutil

import dateutil.parser
import numpy as np
import pydicom.uid
from pydicom.dataset import Dataset, FileDataset

from converter.dicom_converter.bruker_2_dicom.utils.cest_dict import add_cest_dict
from converter.dicom_converter.bruker_2_dicom.utils.read_method import read_method_parameters
from converter.dicom_converter.bruker_2_dicom.utils.read_visupars import read_visupars_parameters


class Bruker2DicomConverter:

    @staticmethod
    def convert(dirs):

        try:
            print("Starting with " + str(dirs[0].split("/")[-1]))

            convert_path = dirs[0]
            dst_path = dirs[1]

            if "Results".lower() in dirs[0].split("/")[-1].lower():
                pass

            else:
                
                dseq_file = glob(convert_path + "/**/2dseq", recursive=True)
                dseq_path = dseq_file[0].replace("\\", "/")
                # Visu_pars file
                visu_pars_path = glob(
                    convert_path + "/**/**/visu_pars", recursive=True
                )[0].replace("\\", "/")
                with open(visu_pars_path, "r"):
                    parameters = read_visupars_parameters(visu_pars_path)
                # Reco file
                reco_path = "/".join(dseq_path.split("/")[:-1]) + "/" + "reco"
                with open(reco_path, "r"):
                    reco_parameters = read_method_parameters(reco_path)
                # Method file
                method_path = convert_path + "/" + "method"
                with open(method_path, "r"):
                    method_parameters = read_method_parameters(method_path)
                # acqp file
                acqp_path = convert_path + "/" + "acqp"
                with open(acqp_path, "r"):
                    acqp_parameters = read_visupars_parameters(acqp_path)

                try:
                    PV_version = parameters.get("VisuCreatorVersion")
                    if PV_version == "":
                        PV_version = acqp_parameters.get("ACQ_sw_version")
                except Exception as e:
                    pass

                filename_little_endian = "MRIm"

                img_type = parameters.get("VisuCoreWordType")
                img_endianness = parameters.get("VisuCoreByteOrder")
                img_frames = parameters.get("VisuCoreFrameCount")
                img_dims = parameters.get("VisuCoreSize")
                core_ext = parameters.get("VisuCoreExtent")

                if isinstance(parameters.get("VisuCoreDataSlope"), str) and isinstance(
                    parameters.get("VisuCoreDataOffs"), str
                ):
                    slope = float(
                        parameters.get("VisuCoreDataSlope")[
                            parameters.get("VisuCoreDataSlope").find("(")
                            + 1 : parameters.get("VisuCoreDataSlope").find(")")
                        ]
                    )
                    intercept = float(
                        parameters.get("VisuCoreDataOffs")[
                            parameters.get("VisuCoreDataOffs").find("(")
                            + 1 : parameters.get("VisuCoreDataOffs").find(")")
                        ]
                    )
                else:
                    slope = parameters.get("VisuCoreDataSlope")
                    intercept = parameters.get("VisuCoreDataOffs")

                # Check endianness and precision
                if img_type == "_32BIT_SGN_INT" and img_endianness == "littleEndian":
                    data_precision = np.dtype("<i4")
                elif img_type == "_32BIT_SGN_INT" and img_endianness == "bigEndian":
                    data_precision = np.dtype(">i4")
                elif img_type == "_16BIT_SGN_INT" and img_endianness == "littleEndian":
                    data_precision = np.dtype("<i2")
                elif img_type == "_16BIT_SGN_INT" and img_endianness == "bigEndian":
                    data_precision = np.dtype(">i2")
                else:
                    raise ValueError("Image data precision is neither 16 nor 32 bit!")
                    

                raw_data = open(dseq_path, "rb")
                img_data_precision = np.fromfile(raw_data, dtype=data_precision)
                raw_data.close()

                """
                The dicom viewer expects unsigned 16-bit images as input.
                So you need to convert the 16-bit signed or 32-bit signed images of the paravision to 16-bit unsigned.
                The conversion is based on the fact that raw image values ​​are now represented by values ​​ranging from 0 to 2^16
                """
                if img_type == "_16BIT_SGN_INT" and img_endianness == "littleEndian":

                    # img = np.array(img_data_precision,np.uint16)

                    if np.size(slope) == 1:
                        img_data_corrected = img_data_precision * slope
                        img_data_corrected += intercept
                    else:
                        img_data_corrected = img_data_precision * slope[0]
                        img_data_corrected += intercept[0]

                    factor = ((2**16) - 1) / (np.amax(img_data_corrected))

                    img_float = img_data_corrected * factor

                    img = np.array(img_float, np.uint16)

                # if 32 bit, slope and intercept correction
                elif img_type == "_32BIT_SGN_INT" and img_endianness == "littleEndian":

                    if np.size(slope) == 1:
                        img_data_corrected = img_data_precision * slope
                        img_data_corrected += intercept
                    else:
                        img_data_corrected = img_data_precision * slope[0]
                        img_data_corrected += intercept[0]

                    factor = ((2**16) - 1) / (np.amax(img_data_corrected))

                    img_float = img_data_corrected * factor

                    img = np.array(img_float, np.uint16)

                else:
                    raise ValueError ("The image you are trying to convert is neither 16 bit nor 32 bit!")               

                # Populate required values for file meta information
                file_meta = Dataset()
                file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.4"
                file_meta.MediaStorageSOPInstanceUID = parameters.get("VisuUid")
                file_meta.ImplementationClassUID = "1.2.276.0.7230010.3.0.3.5.3"
                file_meta.ImplementationVersionName = "OFFIS_DCMTK_353"

                # Create the FileDataset instance (initially no data elements, but file_meta supplied)
                img_data = FileDataset(
                    filename_little_endian,
                    {},
                    file_meta=file_meta,
                    preamble=b"\0" * 128,
                )
                img_data.file_meta.TransferSyntaxUID = (
                    pydicom.uid.ExplicitVRLittleEndian
                )

                # This group is responsible for describing how to read the pixels
                nframes = int(parameters.get("VisuCoreFrameCount"))
                img_data.NumberOfFrames = parameters.get("VisuCoreFrameCount")
                img_data.SamplesPerPixel = 1
                img_data.PhotometricInterpretation = "MONOCHROME2"
                img_data.PixelRepresentation = 0  # unsigned (0)
                img_data.BitsAllocated = 16
                img_data.BitsStored = 16
                img_data.HighBit = 15
                img_data.Columns = int(img_dims[0])
                img_data.Rows = int(img_dims[1])
                img_data.PixelSpacing = [
                    core_ext[0] / img_dims[0],
                    core_ext[1] / img_dims[1],
                ]
                img_data.WindowCenterWidthExplanation = "MinMax"

                # PixelData contains the raw bytes exactly as found in the file
                img_data.PixelData = img.tobytes()
                img_data[0x7FE0, 0x0010].VR = "OW"

                # The dicom_dict entry for LargestImagePixelValue uses an ambiguous VR (Value Representation).
                # It can either be an unsigned short integer (US) or a signed short integer (SS).
                # In order to properly write the data to the file,
                # you have to explicitly tell it which one you want to use.
                img_data.WindowWidth = int(np.amax(img) + 1)
                img_data.WindowCenter = int((np.amax(img) + 1) / 2)
                img_data[0x0028, 0x1050].VR = "DS"
                img_data[0x0028, 0x1051].VR = "DS"
                img_data.SmallestImagePixelValue = int(np.amin(img_data.pixel_array))
                img_data.LargestImagePixelValue = int(np.amax(img_data.pixel_array))
                img_data[0x0028, 0x0106].VR = "US"
                img_data[0x0028, 0x0107].VR = "US"

                add_cest_dict()
                num_digits = len(str(len(img_data.pixel_array)))
                # Loop over the number of frames found in img_data
                for iteration, layer in enumerate(img_data.pixel_array, 0):
                    layer = np.reshape(layer, int(img_dims[0]) * int(img_dims[1]))

                    # Populate required values for file meta information
                    file_meta_temp = Dataset()
                    file_meta_temp.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.4"
                    file_meta_temp.MediaStorageSOPInstanceUID = parameters.get(
                        "VisuUid"
                    ) + ".%s" % (iteration)
                    file_meta_temp.ImplementationClassUID = (
                        "1.2.276.0.7230010.3.0.3.5.3"
                    )
                    file_meta_temp.ImplementationVersionName = "OFFIS_DCMTK_353"

                    # Create the FileDataset instance (initially no data elements, but file_meta supplied)
                    ds_temp = FileDataset(
                        filename_little_endian,
                        {},
                        file_meta=file_meta_temp,
                        preamble=b"\0" * 128,
                    )
                    ds_temp.file_meta.TransferSyntaxUID = (
                        pydicom.uid.ExplicitVRLittleEndian
                    )

                    # Add the data elements -  Check DICOM standard
                    ds_temp.ImageType = ["ORIGINAL", "PRIMARY", "OTHER"]

                    # if ('treated' in root):
                    #    appendix='treated'
                    # elif ('untreated' in root):
                    #    appendix='untreated'

                    # patient_type=re.findall('/[^/]*trea[^/]*/[^/]*/',root)
                    # if len(patient_type)!=0:
                    #    group=patient_type[0].split('/')[1]
                    #    timepoint=patient_type[0].split('/')[2]
                    #    ds.PatientName = parameters.get("VisuSubjectName")+'_'+group+'_'+timepoint
                    # else:
                    #    ds.PatientName= parameters.get("VisuSubjectName")

                    # head_ID, _ = os.path.split(head2)

                    ############### Get common parameters among PV versions ###################
                    ds_temp.PatientName = parameters.get("VisuSubjectName")
                    ds_temp.PatientID = parameters.get("VisuSubjectId")
                    ds_temp.PatientBirthDate = parameters.get("VisuSubjectBirthDate")
                    ds_temp.PatientSex = parameters.get("VisuSubjectSex")
                    ds_temp.PatientWeight = parameters.get("VisuSubjectWeight")
                    ds_temp.PatientComments = parameters.get("VisuSubjectComment")
                    ds_temp.PatientSpeciesDescription = parameters.get(
                        "VisuSubjectType"
                    )
                    ds_temp.ReferringPhysicianName = " ".join(
                        parameters.get("VisuStudyReferringPhysician")
                    )
                    ds_temp.PatientBreedDescription = None
                    ds_temp.PatientBreedCodeSequence = None
                    ds_temp.ResponsiblePerson = None
                    ds_temp.BreedRegistrationSequence = None
                    ds_temp.ResponsibleOrganization = " ".join(
                        parameters.get("VisuInstitution")
                    )

                    ds_temp.StudyID = parameters.get("VisuStudyId")
                    ds_temp.SeriesNumber = os.path.basename(convert_path)
                    ds_temp.Modality = str("MR")
                    ds_temp.ScanningSequence = str("RM")
                    ds_temp.SequenceVariant = str("None")
                    ds_temp.SequenceName = " ".join(
                        parameters.get("VisuAcqSequenceName")
                    )
                    ds_temp.ProtocolName = parameters.get("VisuAcquisitionProtocol")
                    ds_temp.SeriesDescription = parameters.get(
                        "VisuAcquisitionProtocol"
                    )

                    if (
                        np.size(parameters.get("VisuAcqRepetitionTime")) > 1
                        and np.size(parameters.get("VisuAcqRepetitionTime")) == nframes
                    ):
                        ds_temp.RepetitionTime = float(
                            np.array(
                                parameters.get("VisuAcqRepetitionTime"), dtype=int
                            )[iteration]
                        )
                    elif (
                        np.size(parameters.get("VisuAcqRepetitionTime")) > 1
                        and np.size(parameters.get("VisuAcqRepetitionTime")) != nframes
                    ):
                        r_step = int(
                            nframes / np.size(parameters.get("VisuAcqRepetitionTime"))
                        )
                        repetition_time = []
                        for t in range(
                            0, np.size(parameters.get("VisuAcqRepetitionTime"))
                        ):
                            for kk in range(0, r_step):
                                repetition_time.append(
                                    parameters.get("VisuAcqRepetitionTime")[t]
                                )
                        ds_temp.RepetitionTime = float(
                            np.array(repetition_time, dtype=float)[iteration]
                        )
                    else:
                        ds_temp.RepetitionTime = float(
                            parameters.get("VisuAcqRepetitionTime")
                        )

                    if (
                        np.size(parameters.get("VisuAcqEchoTime")) > 1
                        and np.size(parameters.get("VisuAcqEchoTime")) == nframes
                    ):
                        ds_temp.EchoTime = str(
                            np.array(parameters.get("VisuAcqEchoTime"), dtype=float)[
                                iteration
                            ]
                        )
                    elif (
                        np.size(parameters.get("VisuAcqEchoTime")) > 1
                        and np.size(parameters.get("VisuAcqEchoTime")) != nframes
                    ):
                        e_step = int(
                            nframes / np.size(parameters.get("VisuAcqEchoTime"))
                        )
                        echo_time = []
                        for t in range(0, np.size(parameters.get("VisuAcqEchoTime"))):
                            for kk in range(0, e_step):
                                echo_time.append(parameters.get("VisuAcqEchoTime")[t])
                        ds_temp.EchoTime = str(
                            np.array(echo_time, dtype=float)[iteration]
                        )
                    else:
                        ds_temp.EchoTime = parameters.get("VisuAcqEchoTime")

                    ds_temp.AcquisitionDuration = parameters.get("VisuAcqScanTime")
                    ds_temp.NumberOfAverages = str(
                        parameters.get("VisuAcqNumberOfAverages")
                    )
                    ds_temp.ImagingFrequency = parameters.get("VisuAcqImagingFrequency")
                    ds_temp.ImagedNucleus = parameters.get("VisuAcqImagedNucleus")
                    ds_temp.NumberOfPhaseEncodingSteps = parameters.get(
                        "VisuAcqPhaseEncSteps"
                    )
                    ds_temp.EchoTrainLength = parameters.get("VisuAcqEchoTrainLength")
                    ds_temp.PixelBandwidth = parameters.get("VisuAcqPixelBandwidth")
                    ds_temp.FlipAngle = str(parameters.get("VisuAcqFlipAngle"))
                    ds_temp.PatientPosition = parameters.get("VisuSubjectPosition")
                    ds_temp.PatientOrientation = method_parameters.get(
                        "PVM_SPackArrReadOrient"
                    )[0][1:]
                    ds_temp.StationName = parameters.get("VisuStation")
                    ds_temp.InstitutionName = " ".join(
                        parameters.get("VisuInstitution")
                    )
                    ds_temp.Manufacturer = parameters.get("ORIGIN")
                    ds_temp.SeriesInstanceUID = parameters.get("VisuUid")
                    ds_temp.FrameOfReferenceUID = parameters.get("VisuUid") + ".%s" % (
                        iteration
                    )
                    ds_temp.StudyInstanceUID = parameters.get("VisuStudyUid")
                    ds_temp.SOPInstanceUID = parameters.get("VisuUid") + ".%s" % (
                        iteration
                    )
                    ds_temp.SOPClassUID = "1.2.840.10008.5.1.4.1.1.4"
                    ds_temp.AcquisitionNumber = iteration + 1
                    ds_temp.InstanceNumber = iteration + 1
                    ds_temp.MRAcquisitionType = str(parameters.get("VisuCoreDim")) + "D"
                    ds_temp.SoftwareVersions = (
                        str(parameters.get("VisuCreator"))
                        + " "
                        + str(parameters.get("VisuCreatorVersion"))
                    )
                    ds_temp.PercentPhaseFieldOfView = "100"

                    if parameters.get("VisuCoreOrientation").shape[0] == 1:
                        ds_temp.ImagePositionPatient = list(
                            map(str, parameters.get("VisuCorePosition")[0])
                        )
                        ds_temp.ImageOrientationPatient = list(
                            map(str, parameters.get("VisuCoreOrientation")[0][0:6])
                        )
                        ds_temp.SliceThickness = parameters.get(
                            "VisuCoreFrameThickness"
                        )
                        ds_temp.NumberOfFrames = 1
                        ds_temp.ImagesInAcquisition = parameters.get(
                            "VisuCoreFrameCount"
                        )
                        ds_temp.SliceLocation = parameters.get("VisuCorePosition")[0][2]
                    elif parameters.get("VisuCoreOrientation").shape[0] != nframes:
                        p = int(nframes / parameters.get("VisuCorePosition").shape[0])
                        visucoreposition = np.tile(
                            parameters.get("VisuCorePosition"), (p, 1)
                        )
                        visucoreorientation = np.tile(
                            parameters.get("VisuCoreOrientation"), (p, 1)
                        )
                        ds_temp.ImagePositionPatient = list(
                            map(str, visucoreposition[iteration - 1])
                        )
                        ds_temp.ImageOrientationPatient = list(
                            map(str, visucoreorientation[iteration - 1][0:6])
                        )
                        ds_temp.SliceThickness = parameters.get(
                            "VisuCoreFrameThickness"
                        )
                        ds_temp.NumberOfFrames = 1
                        ds_temp.ImagesInAcquisition = parameters.get(
                            "VisuCoreFrameCount"
                        )
                        ds_temp.SliceLocation = list(
                            map(str, visucoreposition[iteration])
                        )[2]
                    else:
                        ds_temp.ImagePositionPatient = list(
                            map(str, parameters.get("VisuCorePosition")[iteration])
                        )
                        ds_temp.ImageOrientationPatient = list(
                            map(
                                str,
                                parameters.get("VisuCoreOrientation")[iteration][0:6],
                            )
                        )
                        ds_temp.SliceThickness = parameters.get(
                            "VisuCoreFrameThickness"
                        )
                        ds_temp.NumberOfFrames = 1
                        ds_temp.ImagesInAcquisition = parameters.get(
                            "VisuCoreFrameCount"
                        )
                        ds_temp.SliceLocation = list(
                            map(str, parameters.get("VisuCorePosition")[iteration])
                        )[2]

                    # Get other parameters which differ among PV versions

                    if PV_version == "5.1":
                        ########################### ParaVision 5.1 ##############################
                        studydate = parameters.get("VisuStudyDate").date()
                        studytime = parameters.get("VisuStudyDate").time()
                        ds_temp.StudyDate = studydate.strftime("%Y%m%d")
                        ds_temp.StudyTime = studytime.strftime("%H%M%S")
                        acquisitiondate = parameters.get("VisuAcqDate").date()
                        acquisitiontime = parameters.get("VisuAcqDate").time()
                        ds_temp.AcquisitionDate = acquisitiondate.strftime("%Y%m%d")
                        ds_temp.AcquisitionTime = acquisitiontime.strftime("%H%M%S")
                        VisuAcqImagePhaseEncDir = parameters.get(
                            "VisuAcqImagePhaseEncDir"
                        )
                        ds_temp.Columns = int(img_dims[0])
                        ds_temp.Rows = int(img_dims[1])
                        if np.size(VisuAcqImagePhaseEncDir) == 1:
                            ds_temp.InPlanePhaseEncodingDirection = parameters.get(
                                "VisuAcqImagePhaseEncDir"
                            ).split("_")[0]
                        else:
                            ds_temp.InPlanePhaseEncodingDirection = parameters.get(
                                "VisuAcqImagePhaseEncDir"
                            )[0].split("_")[0]
                        if ds_temp.InPlanePhaseEncodingDirection == "row":
                            acqmat = np.pad(
                                parameters.get("VisuAcqSize"), 1, "constant"
                            )
                            ds_temp.AcquisitionMatrix = list(
                                np.array(acqmat, dtype=int)
                            )
                            ds_temp.PixelSpacing = [
                                core_ext[0] / img_dims[0],
                                core_ext[1] / img_dims[1],
                            ]
                            pixel_spacing = [
                                core_ext[0] / img_dims[0],
                                core_ext[1] / img_dims[1],
                            ]
                            ds_temp.PixelSpacing = pixel_spacing[::-1]
                        elif ds_temp.InPlanePhaseEncodingDirection == "col":
                            acqmat = np.insert(parameters.get("VisuAcqSize"), 1, [0, 0])
                            ds_temp.AcquisitionMatrix = list(
                                np.flip(np.array(acqmat, dtype=int), 0)
                            )
                            ds_temp.PixelSpacing = [
                                core_ext[1] / img_dims[1],
                                core_ext[0] / img_dims[0],
                            ]
                        gamma = 42.5756
                        Bo = round(ds_temp.ImagingFrequency / gamma)
                        ds_temp.MagneticFieldStrength = Bo

                    elif PV_version == "6.0.1":
                        ########################### ParaVision 6.0.1 ##############################
                        s = parameters.get("VisuStudyDate")
                        date = re.sub("\ |\<|\>", "", s)
                        studydate = dateutil.parser.parse(date)
                        ds_temp.StudyDate = studydate.strftime("%Y%m%d")
                        ds_temp.StudyTime = studydate.strftime("%H%M%S")
                        t = parameters.get("VisuAcqDate")
                        acqdate = re.sub("\ |\<|\>", "", t)
                        acquisitiondate = dateutil.parser.parse(acqdate)
                        ds_temp.AcquisitionDate = acquisitiondate.strftime("%Y%m%d")
                        ds_temp.AcquisitionTime = acquisitiondate.strftime("%H%M%S")
                        ds_temp.InPlanePhaseEncodingDirection = parameters.get(
                            "VisuAcqGradEncoding"
                        )
                        if ds_temp.InPlanePhaseEncodingDirection[0] == "read_enc":
                            ds_temp.Columns = int(img_dims[0])
                            ds_temp.Rows = int(img_dims[1])
                            acqmat = np.pad(
                                parameters.get("VisuAcqSize"), 1, "constant"
                            )
                            ds_temp.AcquisitionMatrix = list(
                                np.array(acqmat, dtype=int)
                            )
                            ds_temp.PixelSpacing = [
                                core_ext[1] / img_dims[1],
                                core_ext[0] / img_dims[0],
                            ]
                        elif ds_temp.InPlanePhaseEncodingDirection[0] == "phase_enc":
                            ds_temp.Columns = int(img_dims[1])
                            ds_temp.Rows = int(img_dims[0])
                            acqmat = np.insert(parameters.get("VisuAcqSize"), 1, [0, 0])
                            ds_temp.AcquisitionMatrix = list(
                                np.flip(np.array(acqmat, dtype=int), 0)
                            )  # check
                            ds_temp.PixelSpacing = [
                                core_ext[0] / img_dims[0],
                                core_ext[1] / img_dims[1],
                            ]
                        gamma = 42.5756
                        Bo = round(ds_temp.ImagingFrequency / gamma)
                        ds_temp.MagneticFieldStrength = Bo

                    elif "360" in PV_version:
                        ########################### ParaVision 360 ##############################
                        VisuStudyDate = parameters.get("VisuStudyDate")
                        StudyDate = re.sub("\ |\<|\>", "", VisuStudyDate)
                        StudyDate = dateutil.parser.parse(StudyDate)
                        ds_temp.StudyDate = StudyDate.strftime("%Y%m%d")
                        ds_temp.StudyTime = StudyDate.strftime("%H%M%S")
                        VisuAcqDate = parameters.get("VisuAcqDate")
                        AcqDate = re.sub("\ |\<|\>", "", VisuAcqDate)
                        AcqDate = dateutil.parser.parse(AcqDate)
                        ds_temp.AcquisitionDate = AcqDate.strftime("%Y%m%d")
                        ds_temp.AcquisitionTime = AcqDate.strftime("%H%M%S")
                        ds_temp.InPlanePhaseEncodingDirection = parameters.get(
                            "VisuAcqGradEncoding"
                        )
                        if ds_temp.InPlanePhaseEncodingDirection[0] == "read_enc":
                            ds_temp.Columns = int(img_dims[0])
                            ds_temp.Rows = int(img_dims[1])
                            acqmat = np.pad(
                                parameters.get("VisuAcqSize"), 1, "constant"
                            )
                            ds_temp.AcquisitionMatrix = list(
                                np.array(acqmat, dtype=int)
                            )
                            ds_temp.PixelSpacing = [
                                core_ext[1] / img_dims[1],
                                core_ext[0] / img_dims[0],
                            ]
                        elif ds_temp.InPlanePhaseEncodingDirection[0] == "phase_enc":
                            ds_temp.Columns = int(img_dims[1])
                            ds_temp.Rows = int(img_dims[0])
                            acqmat = np.insert(parameters.get("VisuAcqSize"), 1, [0, 0])
                            ds_temp.AcquisitionMatrix = list(
                                np.flip(np.array(acqmat, dtype=int), 0)
                            )
                            ds_temp.PixelSpacing = [
                                core_ext[0] / img_dims[0],
                                core_ext[1] / img_dims[1],
                            ]
                        ds_temp.MagneticFieldStrength = parameters.get(
                            "VisuMagneticFieldStrength"
                        )

                    # DCE acquisition
                    if "DCE" in ds_temp.ProtocolName:
                        NRepetitions = int(method_parameters.get("PVM_NRepetitions"))
                        enc_matrix = str(method_parameters.get("PVM_EncMatrix"))
                        enc_step = float(re.findall("[0-9]+", enc_matrix)[1])
                        total_scan_time = (
                            float(ds_temp.RepetitionTime) * NRepetitions * enc_step
                        )
                        scan_time_step = (
                            float(ds_temp.RepetitionTime) * enc_step
                        )  # in ms
                        vect4 = []
                        scan_time = 0
                        step = int(nframes / NRepetitions)
                        for t in range(0, NRepetitions):
                            scan_time = scan_time + scan_time_step
                            for kk in range(0, step):
                                vect4.append(scan_time)
                        ds_temp.TriggerTime = np.array(vect4)[iteration]

                    # DWI acquisition
                    elif (
                        "diffusion" in ds_temp.ProtocolName
                        or "EPI" in ds_temp.ProtocolName
                    ):
                        string = str(method_parameters.get("PVM_DwBvalEach"))
                        b_values = re.findall("[0-9]+", string)
                        # b_values.pop(0)
                        b_values.insert(0, "0")
                        bvalues = []
                        for elem in b_values:
                            bvalues.append(float(elem))
                        vect3 = []
                        step = int(nframes / len(b_values))
                        for t in range(0, len(b_values)):
                            for kk in range(0, step):
                                vect3.append(bvalues[t])
                        ds_temp.DiffusionBValue = np.array(vect3)[iteration]

                    # CEST acquisition
                    elif (
                        "cest" in ds_temp.ProtocolName
                        and method_parameters.get("PVM_SatTransOnOff") == "On"
                    ):
                        ds_temp.Creator = method_parameters.get("OWNER")
                        ds_temp.ChemicalExchangeSaturationType = method_parameters.get(
                            "Method"
                        )
                        ds_temp.SaturationType = method_parameters.get(
                            "PVM_SatTransType"
                        )
                        ds_temp.PulseShape = method_parameters.get(
                            "PVM_SatTransPulseEnum"
                        )
                        ds_temp.PulseLength = float(
                            method_parameters.get("PVM_SatTransPulse")[0]
                        )
                        ds_temp.B1Saturation = method_parameters.get(
                            "PVM_SatTransPulseAmpl_uT"
                        )
                        ds_temp.PulseNumber = int(
                            method_parameters.get("PVM_SatTransNPulses")
                        )
                        # train module needs to be checked
                        if "train" in method_parameters.get("Method"):
                            tau_p = float(method_parameters.get("PVM_SatTransPulse")[0])
                            tau_d = float(
                                method_parameters.get("PVM_SatTransInterPulseDelay")
                            )
                            n = int(method_parameters.get("PVM_SatTransNPulses"))
                            # method_parameters.get("PVM_SatTransModuleTime") #the result of this two lines should be the same
                            # magtransmoduletime = (tau_p + tau_d) * n
                            ds_temp.InterpulseDelay = tau_d
                            ds_temp.DutyCycle = tau_p / (tau_p + tau_d) * 100
                            ds_temp.SaturationLength = method_parameters.get(
                                "PVM_SatTransModuleTime"
                            )
                        else:
                            ds_temp.DutyCycle = "100"
                            ds_temp.SaturationLength = (
                                ds_temp.PulseLength * ds_temp.PulseNumber
                            )
                        NSatFreq = method_parameters.get("PVM_NSatFreq")
                        if NSatFreq != None:
                            f_step = int(nframes / NSatFreq)
                            ds_temp.MeasurementNumber = NSatFreq
                            ds_temp.RecoveryTime = float(
                                ds_temp.RepetitionTime
                            ) - float(ds_temp.PulseLength)
                            freq_list = method_parameters.get(
                                "PVM_SatTransFreqValues"
                            )  # frequency list is formatted differently for CEST Off and On, need to distinguish both cases
                            freq_list_temp = []
                            # p = '[-+]?\d*\.\d+|[-+]?\d+'
                            if (
                                re.search("[-+]?\d*\.\d+|[-+]?\d+", freq_list[0])
                                is not None
                            ):
                                for catch in re.finditer(
                                    "[-+]?\d*\.\d+|[-+]?\d+", freq_list[0]
                                ):
                                    freq_list_temp.append(catch[0])
                            freq_list = freq_list_temp

                            if (
                                "OFF" not in parameters.get("VisuAcquisitionProtocol")
                                and len(freq_list) > 2
                            ):

                                ############## CEST ON ################
                                if (
                                    method_parameters.get("PVM_SatTransFreqUnit")
                                    == "unit_ppm"
                                ):
                                    sat_freq_ppm = np.array(freq_list, dtype=float)
                                    if NSatFreq > 1 and NSatFreq == nframes:
                                        ds_temp.SaturationOffsetPpm = sat_freq_ppm[
                                            iteration
                                        ]
                                        ds_temp.SaturationOffsetHz = (
                                            sat_freq_ppm[iteration]
                                            * ds_temp.ImagingFrequency
                                        )
                                    elif NSatFreq > 1 and NSatFreq != nframes:
                                        SatFreqPpm = []
                                        for t in range(0, NSatFreq):
                                            for kk in range(0, f_step):
                                                SatFreqPpm.append(sat_freq_ppm[t])
                                        ds_temp.SaturationOffsetPpm = str(
                                            np.array(SatFreqPpm, dtype=float)[iteration]
                                        )
                                        sat_freq_hz = (
                                            SatFreqPpm[iteration]
                                            * ds_temp.ImagingFrequency
                                        )
                                        ds_temp.SaturationOffsetHz = sat_freq_hz
                                    ### Tags for CEST ON only
                                    ds_temp.PulseLength2 = float(
                                        method_parameters.get(
                                            "PVM_SatTransPulseLength2"
                                        )
                                    )
                                    ds_temp.ReadoutTime = (
                                        ds_temp.RepetitionTime
                                        - ds_temp.PulseLength
                                        - (ds_temp.PulseLength2 * (f_step - 1))
                                    ) / f_step

                                elif (
                                    method_parameters.get("PVM_SatTransFreqUnit")
                                    == "unit_hz"
                                ):
                                    sat_freq_hz = np.array(freq_list, dtype=float)
                                    if NSatFreq > 1 and NSatFreq == nframes:
                                        ds_temp.SaturationOffsetHz = sat_freq_hz[
                                            iteration
                                        ]
                                        ds_temp.SaturationOffsetPpm = (
                                            sat_freq_hz[iteration]
                                            * ds_temp.ImagingFrequency
                                        )
                                    elif NSatFreq > 1 and NSatFreq != nframes:
                                        SatFreqHz = []
                                        for t in range(0, NSatFreq):
                                            for kk in range(0, f_step):
                                                SatFreqHz.append(sat_freq_hz[t])
                                        ds_temp.SaturationOffsetHz = str(
                                            np.array(SatFreqHz, dtype=float)[iteration]
                                        )
                                        sat_freq_ppm = (
                                            SatFreqHz[iteration]
                                            * ds_temp.ImagingFrequency
                                        )
                                        ds_temp.SaturationOffsetPpm = sat_freq_ppm
                                    ### Tags for CEST ON only
                                    ds_temp.PulseLength2 = float(
                                        method_parameters.get(
                                            "PVM_SatTransPulseLength2"
                                        )
                                    )
                                    ds_temp.ReadoutTime = (
                                        ds_temp.RepetitionTime
                                        - ds_temp.PulseLength
                                        - (ds_temp.PulseLength2 * (f_step - 1))
                                    ) / f_step

                            else:
                                ############## CEST OFF ################
                                if (
                                    method_parameters.get("PVM_SatTransFreqUnit")
                                    == "unit_ppm"
                                ):
                                    sat_freq_ppm = np.array(freq_list, dtype=float)
                                    SatFreqPpm = []
                                    for t in range(0, NSatFreq):
                                        for kk in range(0, f_step):
                                            SatFreqPpm.append(sat_freq_ppm[t])
                                    ds_temp.SaturationOffsetPpm = str(
                                        np.array(SatFreqPpm, dtype=float)[iteration]
                                    )
                                    sat_freq_hz = (
                                        SatFreqPpm[iteration] * ds_temp.ImagingFrequency
                                    )
                                    ds_temp.SaturationOffsetHz = sat_freq_hz
                                    ### Not tested

                                elif (
                                    method_parameters.get("PVM_SatTransFreqUnit")
                                    == "unit_hz"
                                ):
                                    sat_freq_hz = np.array(freq_list, dtype=float)
                                    SatFreqHz = []
                                    for t in range(0, NSatFreq):
                                        for kk in range(0, f_step):
                                            SatFreqHz.append(sat_freq_hz[t])
                                    ds_temp.SaturationOffsetHz = str(
                                        np.array(SatFreqHz, dtype=float)[iteration]
                                    )
                                    sat_freq_ppm = (
                                        SatFreqHz[iteration] * ds_temp.ImagingFrequency
                                    )
                                    ds_temp.SaturationOffsetPpm = sat_freq_ppm

                        elif (
                            NSatFreq == None
                            and method_parameters.get("PVM_SatTransFreqUnit")
                            == "unit_ppm"
                        ):
                            freq_list = method_parameters.get(
                                "PVM_SatTransFreqValues"
                            )  # frequency list is formatted differently for CEST Off and On, need to distinguish both cases
                            freq_list = freq_list[0].split(" ")
                            while "" in freq_list:
                                freq_list.remove("")
                            [elem.strip(" ") for elem in freq_list]
                            sat_freq_ppm = np.array(freq_list, dtype=float)
                            ds_temp.SaturationOffsetPpm = sat_freq_ppm[iteration]
                            ds_temp.SaturationOffsetHz = (
                                sat_freq_ppm[iteration] * ds_temp.ImagingFrequency
                            )
                        elif (
                            NSatFreq == None
                            and method_parameters.get("PVM_SatTransFreqUnit")
                            == "unit_hz"
                        ):
                            freq_list = method_parameters.get(
                                "PVM_SatTransFreqValues"
                            )  # frequency list is formatted differently for CEST Off and On, need to distinguish both cases
                            freq_list = freq_list[0].split(" ")
                            while "" in freq_list:
                                freq_list.remove("")
                            [elem.strip(" ") for elem in freq_list]
                            sat_freq_hz = np.array(freq_list, dtype=float)
                            ds_temp.SaturationOffsetHz = sat_freq_hz[iteration]
                            ds_temp.SaturationOffsetPpm = (
                                sat_freq_hz[iteration] * ds_temp.ImagingFrequency
                            )

                    # Image pixel module with the tags starting with 0028.
                    # This group is responsible for describing how to read the pixels
                    ds_temp.SamplesPerPixel = 1
                    ds_temp.PhotometricInterpretation = "MONOCHROME2"
                    ds_temp.PixelRepresentation = 0
                    ds_temp.BitsAllocated = 16
                    ds_temp.BitsStored = 16
                    ds_temp.HighBit = 15
                    ds_temp.WindowCenterWidthExplanation = "MinMax"
                    ds_temp.PixelData = layer
                    ds_temp[0x7FE0, 0x0010].VR = "OW"
                    max_range = int(np.amax(layer) / factor)
                    min_range = int(np.amin(layer) / factor)
                    ds_temp.WindowWidth = max_range
                    ds_temp.WindowCenter = int((max_range - min_range) / 2)
                    # ds_temp.WindowWidth = int(np.amax(img) + 1)
                    # ds_temp.WindowCenter = int((np.amax(img) + 1) / 2)
                    ds_temp[0x0028, 0x1050].VR = "DS"
                    ds_temp[0x0028, 0x1051].VR = "DS"
                    ds_temp.SmallestImagePixelValue = int(np.amin(layer))
                    ds_temp.LargestImagePixelValue = int(np.amax(layer))
                    ds_temp[0x0028, 0x0106].VR = "US"
                    ds_temp[0x0028, 0x0107].VR = "US"
                    ds_temp.RescaleSlope = 1 / factor
                    for j in range(0, nframes - 1):
                        ds_temp.RescaleIntercept = str(
                            parameters.get("VisuCoreDataOffs")[j]
                        )

                    # Set creation date/time
                    dt = datetime.datetime.now()
                    ds_temp.InstanceCreationDate = dt.strftime("%Y%m%d")
                    timeStr = dt.strftime("%H%M%S")
                    ds_temp.InstanceCreationTime = timeStr

                    ds_temp.NumberOfSlices = acqp_parameters.get("NSLICES")

                    string2 = str(reco_parameters.get("RECO_fov"))
                    vect2 = re.findall("[0-9]+", string2)
                    vect2.pop(0)
                    vect3 = []
                    for elem in vect2:
                        vect3.append(float(elem))
                    ds_temp.ReconstructionFieldOfView = vect3

                    outfile = (
                        f"{filename_little_endian}{iteration+1:0{num_digits}d}.dcm"
                    )
                    # outfile = "%s%s.dcm" % (filename_little_endian, str(iteration + 1))

                    # Save DICOM files in separate slices
                    if not os.path.isdir(dst_path):
                        os.makedirs(dst_path)

                    os.chdir(dst_path)

                    os.chdir(dst_path)
                    ds_temp.is_little_endian = True
                    ds_temp.is_implicit_VR = False
                    ds_temp.save_as(outfile)

            print(str(dirs[0].split("/")[-1]) + " done!")
        except Exception as e:
            raise
