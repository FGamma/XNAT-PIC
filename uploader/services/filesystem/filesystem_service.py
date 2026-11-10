import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from enums.uploader_level import UploaderLevel


class FilesystemService:

    @staticmethod
    def get_list_directory_treeview(path: Path) -> List[Dict]:
        """
        Returns a list of dict with name, path, is_dir.
        """
        try:
            # Sorting criterion:
            # - All directories before files
            # - Within groups (directories and files), case-insensitive
            # alphabetical sorting
            children = sorted(
                path.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower())
            )
        except PermissionError:
            return [{
                "name": "[access denied]",
                "path": path,
                "is_dir": False
            }]
        except FileNotFoundError:
            raise ValueError(f"Path not found: {path}")

        items = []
        for child in children:
            if child.name.startswith("."):
                continue

            items.append({
                "name": child.name,
                "path": child,
                "is_dir": child.is_dir(),
            })

        return items

    @staticmethod
    def get_list_dicom_files(path: Path, level: UploaderLevel) -> List[Path] | None:
        """
        Collect DICOM files from scans within the detected experiment folders.
        """
        if path is None:
            raise ValueError("Input path not set.")

        try:
            experiment = FilesystemService._find_experiments(path, level)
        except (FileNotFoundError, PermissionError, OSError) as e:
            raise RuntimeError(f"Cannot inspect upload path: {path}") from e

        if not experiment:
            return None

        try:
            list_dicom_files = [
                file
                for exp in experiment
                for scan in exp.iterdir() if scan.is_dir()
                for file in scan.iterdir()
                if file.is_file() and file.suffix.lower() in [".dcm", ".dicom"]
            ]
        except (FileNotFoundError, PermissionError, OSError) as e:
            raise RuntimeError(
                "Failed while scanning experiments/scans for DICOM files"
            ) from e

        return list_dicom_files

    @staticmethod
    def create_temp_dicom_upload_directory() -> Path:
        """Create and return a temporary directory for upload processing."""
        tmpdir = tempfile.mkdtemp()
        return Path(tmpdir)

    @staticmethod
    def copy_dicom_file(input_root: Path, dicom_file: Path, tmpdir: Path) -> None:
        """Copy a DICOM file preserving its relative path under ``input_root``"""
        try:
            dst_file = tmpdir / dicom_file.relative_to(input_root)
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dicom_file, dst_file)

        except (ValueError, OSError) as e:
            raise RuntimeError(
                f"Failed copying DICOM file {dicom_file}"
            ) from e

    @staticmethod
    def save_dicom_file(
            input_root: Path,
            dicom_file: Path,
            tmpdir: Path,
            new_dicom_file: Iterable[Tuple[Any, str]]) -> None:
        """
        Save generated DICOM datasets preserving the original folder structure.
        """
        try:
            relative_path = dicom_file.relative_to(input_root)
            relative_dir = relative_path.parent
            dst = tmpdir / relative_dir
            for ds, filename in new_dicom_file:
                out_path = dst / filename
                out_path.parent.mkdir(parents=True, exist_ok=True)
                ds.save_as(out_path, write_like_original=False)
        except (ValueError, OSError, AttributeError, TypeError) as e:
            raise RuntimeError(
                f"Failed saving converted DICOM file {dicom_file}"
            ) from e

    @staticmethod
    def _find_experiments(path: Path, level: UploaderLevel) -> List[Path]:
        """
        Resolve experiment directories according to the selected uploader level.
        """
        experiment_list = []
        if level == UploaderLevel.PROJECT:
            # project → subject → experiment
            experiment_list = [
                exp
                for sub in path.iterdir() if sub.is_dir()
                for exp in sub.iterdir() if exp.is_dir()
            ]

        elif level == UploaderLevel.SUBJECT:
            # subject → experiment
            experiment_list = [exp for exp in path.iterdir() if exp.is_dir()]

        elif level == UploaderLevel.EXPERIMENT:
            experiment_list = [path]

        return experiment_list