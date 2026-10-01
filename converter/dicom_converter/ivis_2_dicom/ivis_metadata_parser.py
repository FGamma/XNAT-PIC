import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from converter.dicom_converter.ivis_2_dicom.ivis_dataclasses.ivis_image_info import IvisImageInfo
from converter.dicom_converter.ivis_2_dicom.ivis_dataclasses.ivis_metadata import IvisMetadata
from converter.dicom_converter.ivis_2_dicom.ivis_dataclasses.ivis_section import IvisSection


class IvisMetadataParser:
    # Matches section headers such as:
    # "*** User Input:" or "*** photographic image:	photograph.TIF"
    SECTION_PATTERN = re.compile(r"^\*\*\*\s*([^:]+):\s*(.*)$")

    # Matches key-value lines such as:
    # "User Name: Mario Rossi"
    KEY_VALUE_PATTERN = re.compile(r"^([^:#\t][^:#]*?):\s*(.*)$")

    # Section names that identify image entries.
    IMAGE_KEYWORDS = {
        "photographic image",
        "luminescent image",
        "fluorescent image",
        "readbiasonly image",
        "readbias image",
        "darkcharge image"
    }

    def __init__(self, metadata_file: Path):
        self.metadata_file = metadata_file
        self.section_counter = defaultdict(int)

    def parse(self) -> IvisMetadata:
        metadata = IvisMetadata()

        current_section = None
        current_image = None

        with open(
            self.metadata_file,
            "r",
            encoding="utf-8",
            errors="ignore",
        ) as f:

            for raw_line in f:
                line = raw_line.rstrip("\n")
                stripped = line.strip()

                # --------------------------------------------------------
                # Blank lines and comments
                # --------------------------------------------------------
                if self._is_blank_or_comment(stripped):
                    self._store_raw_line(
                        line,
                        current_section,
                        current_image,
                    )
                    continue

                # --------------------------------------------------------
                # Section header
                # --------------------------------------------------------
                section_match = self.SECTION_PATTERN.match(stripped)

                if section_match:
                    current_section, current_image = self._parse_section(
                        section_match,
                        metadata,
                    )
                    continue

                # --------------------------------------------------------
                # Key-value pair
                # --------------------------------------------------------
                key_value_match = self.KEY_VALUE_PATTERN.match(stripped)

                if key_value_match:
                    self._parse_key_value(
                        key_value_match,
                        line,
                        current_section,
                        current_image,
                    )
                    continue

                # --------------------------------------------------------
                # Unknown / unparsed line
                # --------------------------------------------------------
                self._store_raw_line(
                    line,
                    current_section,
                    current_image,
                )

        return metadata

    # ====================================================================
    # Section parsing
    # ====================================================================
    def _parse_section(
        self,
        match,
        metadata: IvisMetadata,
    ):
        base_name = match.group(1).strip()
        possible_value = match.group(2).strip() or None

        # A new section starts here, so reset the parsing context.
        current_section = None
        current_image = None

        if base_name.lower() in self.IMAGE_KEYWORDS:
            current_image = self._create_image(
                base_name,
                possible_value,
                metadata,
            )

        else:
            current_section = self._create_section(
                base_name,
                possible_value,
                metadata,
            )

        return current_section, current_image

    def _create_image(
        self,
        section_name: str,
        possible_value: str | None,
        metadata: IvisMetadata,
    ) -> IvisImageInfo:

        filename = self._extract_image_filename(possible_value)

        file_path = (
            self.metadata_file.parent / filename
            if filename
            else None
        )

        image = IvisImageInfo(
            section=section_name,
            filename=filename,
            file_path=file_path,
        )

        metadata.images.append(image)

        return image

    def _create_section(
        self,
        base_name: str,
        possible_value: str | None,
        metadata: IvisMetadata,
    ) -> IvisSection:

        # Generate a unique name for repeated sections.
        count = self.section_counter[base_name]

        name = (
            base_name
            if count == 0
            else f"{base_name} ({count})"
        )

        self.section_counter[base_name] += 1

        section = IvisSection(name=name)

        if possible_value:
            self._store(
                section.metadata,
                base_name,
                possible_value,
            )

        metadata.sections.append(section)

        return section

    # ====================================================================
    # Key-value parsing
    # ====================================================================
    def _parse_key_value(
        self,
        match,
        line: str,
        current_section: IvisSection | None,
        current_image: IvisImageInfo | None,
    ) -> None:

        key = match.group(1).strip()

        # Remove inline comments from the value.
        # Example:
        # "Exposure: 100 # milliseconds" -> "100"
        value = match.group(2).split("#", 1)[0].strip()

        if current_image:
            self._store_image_value(
                current_image,
                key,
                value,
            )
            current_image.raw_lines.append(line)
            return

        if current_section:
            self._store_section_value(
                current_section,
                key,
                value,
            )
            current_section.raw_lines.append(line)

    def _store_image_value(
        self,
        image: IvisImageInfo,
        key: str,
        value: str,
    ) -> None:

        # If the filename was not found in the section header,
        # try to identify it from a key-value entry.
        if (
            image.filename is None
            and value.lower().endswith((".tif", ".tiff"))
        ):
            image.filename = value
            image.file_path = self.metadata_file.parent / value
            return

        self._store(
            image.metadata,
            key,
            value,
        )

    def _store_section_value(
        self,
        section: IvisSection,
        key: str,
        value: str,
    ) -> None:

        self._store(
            section.metadata,
            key,
            value,
        )

    # ====================================================================
    # Raw line handling
    # ====================================================================
    @staticmethod
    def _is_blank_or_comment(line: str) -> bool:
        return not line or line.startswith("#")

    @staticmethod
    def _store_raw_line(
        line: str,
        current_section: IvisSection | None,
        current_image: IvisImageInfo | None,
    ) -> None:

        if current_section:
            current_section.raw_lines.append(line)

        if current_image:
            current_image.raw_lines.append(line)

    # ====================================================================
    # Helpers
    # ====================================================================
    @staticmethod
    def _extract_image_filename(
        value: str | None,
    ) -> str | None:

        if value and value.lower().endswith((".tif", ".tiff")):
            return value

        return None

    @staticmethod
    def _store(
        target: dict,
        key: str,
        value: Any,
    ) -> None:

        if key not in target:
            target[key] = value
            return

        existing = target[key]

        if isinstance(existing, list):
            if value != existing[-1]:
                existing.append(value)
        else:
            if value != existing:
                target[key] = [existing, value]