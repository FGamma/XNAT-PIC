from dataclasses import dataclass, field

from converter.dicom_converter.ivis_2_dicom.ivis_dataclasses.ivis_image_info import IvisImageInfo
from converter.dicom_converter.ivis_2_dicom.ivis_dataclasses.ivis_section import IvisSection

@dataclass
class IvisMetadata:
    sections: list[IvisSection] = field(default_factory=list)
    images: list[IvisImageInfo] = field(default_factory=list)

    def get_sections(self):
        return self.sections

    def get_images(self):
        return self.images