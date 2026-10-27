import re
from pathlib import Path

import unicodedata

from uploader.services.dicom.dicom_compatibility_service import \
    DicomCompatibilityService
from uploader.services.dicom.dicom_modify_modality import DicomModifyModality
from uploader.services.dicom.dicom_validator_service import \
    DicomValidatorService
from uploader.services.filesystem.filesystem_service import FilesystemService
from enums.uploader_level import UploaderLevel


class ModelUploader:
    def __init__(self):
        self._input_root = None
        self._level = None
        self._tmp_folder_to_upload = None

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
    def level(self):
        return self._level

    @level.setter
    def level(self, level):
        self._level = level

    @property
    def tmp_folder_to_upload(self):
        return self._tmp_folder_to_upload

    @tmp_folder_to_upload.setter
    def tmp_folder_to_upload(self, tmp_folder_to_upload):
        self._tmp_folder_to_upload = tmp_folder_to_upload

    def reset_state(self):
        """Reset all transient uploader state used by a single workflow."""
        self._input_root = None
        self._tmp_folder_to_upload = None
        self._level = None

    def get_dicom_files(self) -> list[Path] | None:
        if self._level and self._level.value == "file":
            self._tmp_folder_to_upload = self._input_root
            return None
        list_dicom_files = FilesystemService.get_list_dicom_files(
            self._input_root, self._level)

        if not list_dicom_files:
            raise ValueError(
                f"The folder\n{self._input_root}\ndoes not contain DICOM files.\n"
                f"Make sure you have selected a valid {self.level}."
            )
        return list_dicom_files

    def validate_dicom_files(self, list_dicom_files: list[Path] | None) -> dict[str, int | list[dict[str, str]]]:
        if self._level and self._level.value == "file":
            validate_dicom_report = {
                "total": 1,
                "copied": 1,
                "converted": 0,
                "failed": [],
            }
            return validate_dicom_report

        if list_dicom_files is None:
            raise ValueError("No DICOM files provided for validation.")

        try:
            tmp_dir = FilesystemService.create_temp_dicom_upload_directory()
            self._tmp_folder_to_upload = tmp_dir / self._input_root.name
            self._tmp_folder_to_upload.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise RuntimeError("Could not prepare temporary upload directory") from e

        validate_dicom_report = {
            "total": len(list_dicom_files),
            "copied": 0,
            "converted": 0,
            "failed":  []
        }
        study_instance_uid_map = {}

        for dicom_file in list_dicom_files:
            try:
                if DicomValidatorService.is_valid_dicom_file(dicom_file):
                    FilesystemService.copy_dicom_file(self._input_root,
                                                      dicom_file,
                                                      self._tmp_folder_to_upload)
                    validate_dicom_report["copied"] += 1
                    continue

                exp_uid_map = DicomCompatibilityService.update_study_instance_uid_map(
                    dicom_file, study_instance_uid_map)
                new_dicom_file = DicomCompatibilityService.get_compatible_dicom_file(
                    dicom_file, exp_uid_map)
                if not new_dicom_file:
                    raise RuntimeError("No compatible DICOM file generated")

                FilesystemService.save_dicom_file(self._input_root,
                                                  dicom_file,
                                                  self._tmp_folder_to_upload,
                                                  new_dicom_file)
                validate_dicom_report["converted"] += 1
            except Exception as e:
                validate_dicom_report["failed"].append({"file": str(dicom_file), "reason": str(e)})
                continue

        return validate_dicom_report

    def build_upload_targets(self, selected_subject_id: str | None,
                             selected_experiment_id: str | None) -> list:
        """
        Build upload targets according to current uploader level.
        """
        if not self._tmp_folder_to_upload:
            raise ValueError("Select a folder to upload.")

        base_path = Path(self._tmp_folder_to_upload)
        level = self._level

        try:
            if level == UploaderLevel.PROJECT:
                subjects = [p for p in base_path.iterdir() if p.is_dir()]
                if not subjects:
                    raise ValueError("No subject folders found in selected path.")

                upload_targets = []
                for subj_folder in subjects:
                    subject_id = self._selected_or_normalized(
                        selected_subject_id,
                        subj_folder.name,
                    )
                    experiments = [e for e in subj_folder.iterdir() if e.is_dir()]
                    for exp_folder in experiments:
                        experiment_id = self._selected_or_normalized(
                            selected_experiment_id,
                            exp_folder.name,
                        )
                        upload_targets.append((exp_folder, subject_id, experiment_id))
                return upload_targets

            if level == UploaderLevel.SUBJECT:
                subject_id = self._selected_or_normalized(
                    selected_subject_id,
                    base_path.name,
                )
                experiments = [e for e in base_path.iterdir() if e.is_dir()]
                return [
                    (
                        exp_folder,
                        subject_id,
                        self._selected_or_normalized(selected_experiment_id,
                                                     exp_folder.name),
                    )
                    for exp_folder in experiments
                ]

            if level == UploaderLevel.EXPERIMENT:
                source_experiment = self._input_root
                source_subject_name = source_experiment.parent.name

                subject_id = self._selected_or_normalized(
                    selected_subject_id,
                    source_subject_name,
                )
                experiment_id = self._selected_or_normalized(
                    selected_experiment_id,
                    base_path.name,
                )
                return [(base_path, subject_id, experiment_id)]
        except OSError as err:
            raise RuntimeError(
                f"Cannot inspect upload folder structure: {err}"
            ) from err

        raise ValueError("Selected upload level is not supported.")

    def validate_resource_upload_context(self, selected_subject_id: str | None,
                                         selected_experiment_id: str | None):
        if self._level != UploaderLevel.FILE:
            return
        if not selected_subject_id:
            raise ValueError("Select an XNAT subject before uploading resources.")
        if not selected_experiment_id:
            raise ValueError(
                "Select an XNAT experiment before uploading resources."
            )

    @staticmethod
    def modify_modality(dicom_path: Path, new_modality: str):
        if not new_modality or not str(new_modality).strip():
            raise ValueError("Select a valid modality value.")

        dicom_files_to_modify = []

        try:
            if dicom_path.is_file():
                if dicom_path.suffix.lower() in [".dcm", ".dicom"]:
                    dicom_files_to_modify.append(dicom_path)
                else:
                    raise ValueError("It is not a dicom file (.dcm o .dicom)")

            elif dicom_path.is_dir():
                dicom_files_to_modify = list(dicom_path.rglob("*.dcm")) + list(
                    dicom_path.rglob("*.dicom"))

                if not dicom_files_to_modify:
                    raise ValueError("Folder does not contain dicom file")

            else:
                raise ValueError("Path does not exist")
        except OSError as err:
            raise RuntimeError(
                f"Cannot inspect selected path for modality update: {err}"
            ) from err

        DicomModifyModality.modify_modality(dicom_files_to_modify, new_modality)

    def _selected_or_normalized(self, selected_value: str | None,
                                fallback_name: str) -> str:
        if selected_value:
            return selected_value
        return self._normalize_id(fallback_name)

    @staticmethod
    def _normalize_id(name: str) -> str:
        normalized = unicodedata.normalize("NFKD", name.strip())
        normalized = normalized.encode("ascii", "ignore").decode("ascii")
        normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", normalized)
        normalized = normalized.strip("_")

        if not normalized:
            raise ValueError("The name cannot be empty")

        if len(normalized) > 64:
            raise ValueError("Name exceeds 64 characters")

        return normalized

