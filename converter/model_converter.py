from pathlib import Path

from converter.dicom_converter.bruker_2_dicom.bruker_2_dicom_converter import \
    Bruker2DicomConverter
from converter.dicom_converter.ivis_2_dicom.ivis_2_dicom_converter import \
    Ivis2DicomConverter
from converter.services.filesystem_service import FilesystemService
from enums.converter_type import ConverterType


class ModelConverter:
    def __init__(self):
        self._input_root = None
        self._output_root = None
        self._level = None
        self._conversion_type = None
        self._input_scans = []
        self._output_scans = []

    def reset_level(self):
        self._input_root = None
        self._output_root = None
        self._level = None
        self._conversion_type = None
        self._input_scans = []
        self._output_scans = []

    def create_dicom_output_folder(self, overwrite):
        FilesystemService.create_dicom_output_folder(self._output_root,
                                                     overwrite)

    def get_input_scans(self):
        self._input_scans = FilesystemService.get_input_scans(
            self._input_root, self.level, self.conversion_type)

    def get_output_scans(self):
        self._output_scans = FilesystemService.get_output_scans(
            self._input_scans,
            self._input_root,
            self._output_root)

    def dicom_converter(self, src_dst):
        if self.conversion_type == ConverterType.BRUKER2DICOM:
            Bruker2DicomConverter.convert(src_dst)
        elif self.conversion_type == ConverterType.IVIS2DICOM:
            ivis2dicom = Ivis2DicomConverter()
            ivis2dicom.convert(src_dst)

    @staticmethod
    def delete_scan_folder_failed(dst: Path):
        FilesystemService.delete_folder(dst)

    @property
    def input_root(self):
        return self._input_root

    @input_root.setter
    def input_root(self, input_root):
        p = Path(input_root)
        if not p.is_dir():
            raise ValueError(f"'{p}' is not a valid folder.")
        self._input_root = p

    @property
    def output_root(self):
        return self._output_root

    @output_root.setter
    def output_root(self, input_root):
        p = input_root
        self._output_root = p.parent / (p.name + "_dcm")

    @property
    def level(self):
        return self._level

    @level.setter
    def level(self, level):
        self._level = level

    @property
    def conversion_type(self):
        return self._conversion_type

    @conversion_type.setter
    def conversion_type(self, conversion_type):
        self._conversion_type = conversion_type

    @property
    def input_scans(self):
        return self._input_scans

    @input_scans.setter
    def input_scans(self, input_scans):
        self._input_scans = input_scans

    @property
    def output_scans(self):
        return self._output_scans

    @output_scans.setter
    def output_scans(self, output_scans):
        self._output_scans = output_scans





