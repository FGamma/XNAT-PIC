from dataclasses import dataclass, field

@dataclass
class IvisSection:
    name: str
    metadata: dict[str, str] = field(default_factory=dict)
    raw_lines: list[str] = field(default_factory=list)

    def get_metadata_value(self, value):
        return self.metadata.get(value)