from dataclasses import field, dataclass
from pathlib import Path
from typing import Dict

@dataclass
class IvisImageInfo:
    section: str
    filename: str
    file_path: Path
    metadata: Dict[str, str] = field(default_factory=dict)
    raw_lines: list[str] = field(default_factory=list)

    def get_metadata_value(self, value):
        return self.metadata.get(value)