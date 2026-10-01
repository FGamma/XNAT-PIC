import re
from pathlib import Path
from collections import defaultdict

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

    # Section names that identify images in the metadata file.
    IMAGE_KEYWORDS = {
        "photographic image",
        "luminescent image",
        "fluorescent image",
        "readbiasonly image",
    }

    def __init__(self, metadata_file: Path):
        self.metadata_file = metadata_file
        # Keeps track of repeated section names so that they can be
        # stored with unique names (e.g. "Acquisition", "Acquisition (1)").
        self.section_counter = defaultdict(int)

    def parse(self):
        metadata = IvisMetadata()

        current_section = None
        current_image = None

        with open(self.metadata_file, "r", encoding="utf-8", errors="ignore") as f:
            for raw_line in f:
                line = raw_line.rstrip("\n")
                stripped = line.strip()

                # --------------------------------------------------------
                # Blank lines and comments
                # --------------------------------------------------------
                # These lines do not contain metadata, but are preserved
                # so that the original structure of the file is retained.
                if not stripped or stripped.startswith("#"):
                    if current_section:
                        current_section.raw_lines.append(line)
                    if current_image:
                        current_image.raw_lines.append(line)
                    continue

                # --------------------------------------------------------
                # Section header
                # --------------------------------------------------------
                sec = self.SECTION_PATTERN.match(stripped)
                if sec:
                    base_name = sec.group(1).strip()
                    possible_value = sec.group(2).strip() or None

                    # A new section starts here, so reset the current
                    # parsing context.
                    current_section = None
                    current_image = None

                    # ----------------------------------------------------
                    # Image section
                    # ----------------------------------------------------
                    if base_name.lower() in self.IMAGE_KEYWORDS:
                        filename = (
                            possible_value
                            if possible_value
                               and possible_value.lower().endswith(
                                (".tif", ".tiff"))
                            else None
                        )
                        if filename:
                            file_path = self.metadata_file.parent / filename
                            current_image = IvisImageInfo(
                                section=base_name, filename=filename,
                                file_path=file_path
                            )
                        current_image.raw_lines.append(line)
                        metadata.images.append(current_image)
                        continue

                    # ----------------------------------------------------
                    # Regular section - No image section
                    # ----------------------------------------------------
                    # Generate a unique name for repeated sections.
                    count = self.section_counter[base_name]
                    name = base_name if count == 0 else f"{base_name} ({count})"
                    self.section_counter[base_name] += 1

                    current_section = IvisSection(name=name)
                    current_section.raw_lines.append(line)
                    metadata.sections.append(current_section)

                    if possible_value:
                        self._store(
                            current_section.metadata,
                            base_name,
                            possible_value,
                        )
                    continue

                # --------------------------------------------------------
                # Key-value pair
                # --------------------------------------------------------
                kv = self.KEY_VALUE_PATTERN.match(stripped)
                if kv:
                    key = kv.group(1).strip()
                    value_raw = kv.group(2)

                    # Remove inline comments from the value.
                    # Example: "Exposure: 100 # milliseconds" -> "100".
                    value_clean = value_raw.split("#", 1)[0].strip()
                    value = value_clean

                    # ----------------------------------------------------
                    # Key-value pair belonging to an image
                    # ----------------------------------------------------
                    if current_image:
                        if (
                                current_image.filename is None
                                and isinstance(value, str)
                                and value.lower().endswith((".tif", ".tiff"))
                        ):
                            current_image.filename = value
                        else:
                            self._store(current_image.metadata, key, value)

                        current_image.raw_lines.append(line)
                        continue

                    # ----------------------------------------------------
                    # Key-value pair belonging to a regular section
                    # ----------------------------------------------------
                    if current_section:
                        self._store(current_section.metadata, key, value)
                        current_section.raw_lines.append(line)
                        continue

                # --------------------------------------------------------
                # Unparsed line
                # --------------------------------------------------------
                # Preserve lines that do not match any known syntax.
                # This prevents information from being lost even when
                # the parser does not understand the line.
                if current_section:
                    current_section.raw_lines.append(line)
                if current_image:
                    current_image.raw_lines.append(line)

        return metadata

    @staticmethod
    def _store(target: dict, key: str, value: Any):
        # Store the first occurrence of a key directly.
        if key not in target:
            target[key] = value
            return

        existing = target[key]

        # Multiple different values for the same key are stored as a list.
        # Duplicate consecutive values are ignored.
        if isinstance(existing, list):
            if value != existing[-1]:
                existing.append(value)
        else:
            if value != existing:
                target[key] = [existing, value]