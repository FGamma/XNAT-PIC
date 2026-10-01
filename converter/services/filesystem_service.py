import shutil
from pathlib import Path
from typing import List, Dict

from enums.converter_level import ConverterLevel
from enums.converter_type import ConverterType


class FilesystemService:
    @staticmethod
    def get_input_scans(input_root: Path, level: ConverterLevel,
                        conversion_type: ConverterType) -> list[Path]:

        if input_root is None:
            raise ValueError("Input path not set.")
        if not input_root.exists():
            raise FileNotFoundError(f"Input folder '{input_root}' does not exist.")
        if not input_root.is_dir():
            raise NotADirectoryError(f"'{input_root}' is not a valid folder.")
        if not any(input_root.iterdir()):
            raise ValueError(
                f"The selected folder '{input_root}' is empty."
            )

        experiment_list = FilesystemService._find_experiments(input_root,
                                                              level)

        if not experiment_list:
            raise ValueError(
                f"No experiments found in '{input_root}' for level '{level.value}'."
            )

        return FilesystemService._filter_scans(experiment_list,
                                               conversion_type)

    @staticmethod
    def get_output_scans(input_scans, input_root, output_root):
        if output_root is None or input_root is None:
            raise ValueError("Paths not set.")
        scan_converted = [
            output_root / p.relative_to(input_root)
            for p in input_scans
        ]
        return scan_converted

    @staticmethod
    def create_dicom_output_folder(output_root: Path, overwrite: bool):
        if output_root is None:
            raise ValueError("Output path not set.")

        if output_root.exists():
            if output_root.is_dir():
                if overwrite:
                    print(
                        f"The folder '{output_root}' already exists."
                        f" Overwrite..."
                    )
                    shutil.rmtree(output_root)
                    output_root.mkdir(parents=True, exist_ok=True)
                else:
                    raise FileExistsError(
                        f"The folder '{output_root}' already exists and "
                        f"'overwrite' is False."
                    )
            else:
                raise NotADirectoryError(
                    f"'{output_root}' exists but is not a directory."
                )
        else:
            print(f"Create the folder '{output_root}'.")
            output_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def delete_folder(dst: Path):
        if dst.is_dir():
            shutil.rmtree(dst)

    @staticmethod
    def _find_experiments(path: Path, level: ConverterLevel) -> list[Path]:
        experiment_list = []
        if level == ConverterLevel.PROJECT:
            # project → subject → experiment
            experiment_list = [
                exp
                for sub in path.iterdir() if sub.is_dir()
                for exp in sub.iterdir() if exp.is_dir()
            ]

        elif level == ConverterLevel.SUBJECT:
            # subject → experiment
            experiment_list = [exp for exp in path.iterdir() if exp.is_dir()]

        elif level == ConverterLevel.EXPERIMENT:
            experiment_list = [path]

        return experiment_list

    @staticmethod
    def _is_bruker_scan(scan: Path) -> bool:
        """A Bruker scan is valid if a '2dseq' file appears in ("*/*/2dseq")."""
        has_2dseq = any(
            (level2 / "2dseq").is_file()
            for level1 in scan.iterdir()
            if level1.is_dir()
            for level2 in level1.iterdir()
            if level2.is_dir()
        )
        return has_2dseq

    @staticmethod
    def _is_ivis_scan(scan: Path) -> bool:
        """An IVIS scan is valid if it contains tif with 'clickinfo'
        in the name."""

        has_tiff = any(
            f.is_file() and f.suffix.lower() in {".tif", ".tiff"}
            for f in scan.iterdir()
        )

        has_clickinfo = any(
            f.is_file() and "clickinfo".lower() in f.name.lower()
            for f in scan.iterdir()
        )

        return has_tiff and has_clickinfo

    @staticmethod
    def _filter_scans(experiment_list: List[Path],
                      conversion_type: ConverterType) -> list[Path]:
        scans = []
        for exp in experiment_list:
            for scan in exp.iterdir():
                if not scan.is_dir():
                    continue

                if conversion_type == ConverterType.BRUKER2DICOM:
                    if FilesystemService._is_bruker_scan(scan):
                        scans.append(scan)

                elif conversion_type == ConverterType.IVIS2DICOM:
                    if FilesystemService._is_ivis_scan(scan):
                        scans.append(scan)

        return scans