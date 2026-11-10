from pathlib import Path
from typing import Iterable

from pydicom import dcmread
from pydicom.errors import InvalidDicomError
from pydicom.uid import SecondaryCaptureImageStorage


class DicomModifyModality:
    @staticmethod
    def modify_modality(
        dicom_files_to_modify: Iterable[Path],
        new_modality: str,
    ) -> None:
        """Update the ``Modality`` tag for each provided DICOM file."""
        if not new_modality or not str(new_modality).strip():
            raise ValueError("New modality value is empty.")

        failed_files = []

        for dicom_scan in dicom_files_to_modify:
            try:
                ds = dcmread(dicom_scan)
                ds.file_meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
                ds.ImageType = ["DERIVED", "SECONDARY", "OTHER"]
                ds.Modality = str(new_modality).strip()
                dicom_scan.parent.mkdir(parents=True, exist_ok=True)
                ds.save_as(dicom_scan, write_like_original=False)
            except (OSError, InvalidDicomError, ValueError, TypeError) as err:
                failed_files.append({"file": str(dicom_scan), "reason": str(err)})

        if failed_files:
            details = "\n".join(
                f"{entry['file']}: {entry['reason']}"
                for entry in failed_files[:5]
            )
            if len(failed_files) > 5:
                details += "\n..."
            raise RuntimeError(
                "Failed to update DICOM modality for one or more files. "
                f"Failures: {len(failed_files)}\n{details}"
            )