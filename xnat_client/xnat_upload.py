import shutil
import tempfile
import time
from pathlib import Path

from xnat_client.xnat_session import XnatSession


class XnatUpload:
    def __init__(self, xnat_session: XnatSession):
        if not xnat_session.session:
            raise ValueError("XNAT session not connected.")
        self._session = xnat_session.session

    def upload_dicom(self,
                     exp_folder,
                     project_id,
                     subject_id,
                     experiment_id,
                     max_attempts=9,
                     poll_interval_seconds=10.0):

        exp_folder = Path(exp_folder)

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir = Path(tmpdir)

                zip_dst = shutil.make_archive(
                    base_name=str(tmpdir / experiment_id),
                    format="zip",
                    root_dir=str(exp_folder)
                )

                imported_session = self._session.services.import_(
                    zip_dst,
                    project=project_id,
                    subject=subject_id,
                    experiment=experiment_id,
                    import_handler="DICOM-zip",
                    overwrite="append",
                    quarantine=False,
                )

                self._session.clearcache()
                self._rebuild_and_archive_imported_session(
                    project_id=project_id,
                    subject_id=subject_id,
                    experiment_id=experiment_id,
                    imported_session=imported_session,
                    max_attempts=max_attempts,
                    poll_interval_seconds=poll_interval_seconds,
                )
        except Exception as e:
            raise RuntimeError(f"Upload failed: {e}") from e

    def upload_files_resources(
            self,
            source_folder,
            project_id,
            subject_id,
            experiment_id,
            default_resource_label="NON_DICOM",
    ):
        source_folder = Path(source_folder)

        resource_label = source_folder.name if source_folder.name else default_resource_label

        if not source_folder.exists() or not source_folder.is_dir():
            raise ValueError("Resource source folder is not valid.")

        if not project_id or not subject_id or not experiment_id:
            raise ValueError(
                "Project, subject, and experiment are required "
                "to upload resources."
            )

        files_by_resource = self._group_resource_files(
            source_folder,
            resource_label,
        )

        if not files_by_resource:
            raise ValueError("No non-DICOM files found to upload.")

        uploaded_files = 0
        failed_uploads = []
        for resource_label, resources in files_by_resource.items():
            try:
                resource = self._get_or_create_experiment_resource(
                    project_id,
                    subject_id,
                    experiment_id,
                    resource_label,
                )
            except KeyError as err:
                raise ValueError(
                    "Invalid XNAT target. Verify project, subject and experiment "
                    "exist and are accessible."
                ) from err

            for local_file, remote_path in resources:
                try:
                    self._upload_resource_file(resource, local_file, remote_path)
                    uploaded_files += 1
                except Exception as err:
                    failed_uploads.append(
                        f"{local_file.name}: {err}"
                    )

        self._session.clearcache()

        if failed_uploads:
            failures_preview = "; ".join(failed_uploads[:5])
            if len(failed_uploads) > 5:
                failures_preview += "; ..."
            raise RuntimeError(
                "Resources upload completed with errors "
                f"({uploaded_files} uploaded, {len(failed_uploads)} failed). "
                f"Failures: {failures_preview}"
            )
        return uploaded_files

    def experiment_exists(self, project_id: str, subject_id: str, experiment_id: str) -> bool:
        if not project_id or not subject_id or not experiment_id:
            return False

        try:
            experiments = self.list_experiments(project_id, subject_id)
        except Exception:
            return False

        return any(
            e["id"] == experiment_id or e["label"] == experiment_id
            for e in experiments
        )

    def list_experiments(self, project_id, subject_id) -> list[dict[str, str]]:
        subj = self._session.projects[project_id].subjects[subject_id]

        return [
            {"id": e.id, "label": e.label or e.id}
            for e in subj.experiments.values()
        ]

    def _group_resource_files(self, source_folder: Path,
                              resource_label: str):
        sanitized_label = self._sanitize_label(resource_label)
        grouped = {sanitized_label: []}
        for local_file in source_folder.rglob("*"):
            if not local_file.is_file():
                continue

            if local_file.suffix.lower() in {".dcm", ".dicom"}:
                continue

            relative_path = local_file.relative_to(source_folder)
            remote_path = relative_path.as_posix()

            grouped[sanitized_label].append(
                (local_file, remote_path)
            )

        if not grouped[sanitized_label]:
            return {}

        return grouped

    def _get_or_create_experiment_resource(self,
                                           project_id: str,
                                           subject_id: str,
                                           experiment_id: str,
                                           resource_label: str):

        session = self._session
        xnat_experiment = session.projects[project_id].subjects[subject_id].experiments[experiment_id]
        resource_label = self._sanitize_label(resource_label)

        if not resource_label in xnat_experiment.resources:
            self._session.classes.ResourceCatalog(
                parent=xnat_experiment,
                label=resource_label,
            )
            self._session.clearcache()

        resource = xnat_experiment.resources[resource_label]
        return resource

    def _rebuild_and_archive_imported_session(
            self,
            project_id: str,
            subject_id: str,
            experiment_id: str,
            imported_session,
            max_attempts: int,
            poll_interval_seconds: float,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds must be >= 0")

        last_status = "unknown"
        last_state_class = "unknown"
        last_action_error = None
        fetch_failures = 0
        max_fetch_failures = max(3, min(6, max_attempts))
        max_rebuild_requests = min(max_attempts, 3)
        max_archive_requests = 1
        rebuild_requests = 0
        archive_requests = 0
        rebuild_dispatched = False
        archive_dispatched = False

        reusable_session = self._as_prearchive_session(imported_session)
        for attempt in range(1, max_attempts + 1):
            if self.experiment_exists(project_id, subject_id, experiment_id):
                return
            prearchive_session = reusable_session
            reusable_session = None
            if prearchive_session is None:
                try:
                    prearchive_session = self._find_prearchive_session(
                        project_id=project_id,
                        subject_id=subject_id,
                        experiment_id=experiment_id,
                    )
                    fetch_failures = 0
                except Exception as err:
                    fetch_failures += 1
                    last_action_error = err
                    if fetch_failures >= max_fetch_failures:
                        raise RuntimeError(
                            "Repeated failures while fetching prearchive session state; "
                            f"aborting after {fetch_failures} failures."
                        ) from err
                    if attempt < max_attempts:
                        time.sleep(poll_interval_seconds)
                    continue

            if prearchive_session is None:
                if last_status and (
                    self._is_in_progress_prearchive_status(last_status)
                    or self._is_transient_prearchive_status(last_status)
                ):
                    return

                if rebuild_dispatched or archive_dispatched:
                    return

                if attempt < max_attempts:
                    time.sleep(poll_interval_seconds)
                    continue

                break

            status = str(getattr(prearchive_session, "status", "") or "").strip().lower()
            last_status = status or "unknown"

            last_state_class = self._classify_prearchive_status(last_status)

            if last_state_class == "receiving":
                if rebuild_requests >= max_rebuild_requests:
                    raise RuntimeError(
                        "Prearchive session remained in RECEIVING after "
                        f"{rebuild_requests} rebuild request(s)."
                    ) from last_action_error

                try:
                    rebuild_requests += 1
                    prearchive_session.rebuild(asynchronous=False)
                    rebuild_dispatched = True
                except Exception as err:
                    rebuild_dispatched = True
                    last_action_error = err

                self._session.clearcache()
                if attempt < max_attempts:
                    time.sleep(poll_interval_seconds)
                continue

            if last_state_class == "transient":
                return

            if last_state_class == "in_progress":
                if attempt < max_attempts:
                    time.sleep(poll_interval_seconds)
                    continue
                return

            if last_state_class == "archivable":
                if archive_requests >= max_archive_requests:
                    if attempt < max_attempts:
                        time.sleep(poll_interval_seconds)
                        continue
                    break
                try:
                    archive_requests += 1
                    prearchive_session.archive(
                        overwrite="append",
                        quarantine=False,
                        project=project_id,
                        subject=subject_id,
                        experiment=experiment_id,
                    )
                    archive_dispatched = True
                except Exception as err:
                    archive_dispatched = True
                    last_action_error = err

                self._session.clearcache()
            elif last_state_class in {"error", "unknown"}:
                raise RuntimeError(
                    "Prearchive session reached a terminal/non-actionable status "
                    f"'{last_status}' for project '{project_id}', subject '{subject_id}', "
                    f"experiment '{experiment_id}'."
                )
            else:
                raise RuntimeError(
                    "Prearchive session reached an unsupported status "
                    f"'{last_status}' for project '{project_id}', subject '{subject_id}', "
                    f"experiment '{experiment_id}'."
                )
            if self.experiment_exists(project_id, subject_id, experiment_id):
                return

            if attempt < max_attempts:
                time.sleep(poll_interval_seconds)

        if last_status and (
            self._is_in_progress_prearchive_status(last_status)
            or self._is_transient_prearchive_status(last_status)
        ):
            return

        if rebuild_dispatched or archive_dispatched:
            if last_action_error is None:
                return

            raise RuntimeError(
                "Import session action(s) may have been accepted server-side, "
                "but the local client observed errors and could not verify completion. "
                f"Last prearchive status: {last_status}. "
                f"Last state class: {last_state_class}. "
                f"rebuild_requests={rebuild_requests}, archive_requests={archive_requests}."
            ) from last_action_error

        raise RuntimeError(
            "Unable to archive imported session after "
            f"{max_attempts} attempts. Last prearchive status: {last_status}. "
            f"Last state class: {last_state_class}. rebuild_requests={rebuild_requests}, "
            f"archive_requests={archive_requests}."
        )

    def _find_prearchive_session(self,
                                 project_id: str,
                                 subject_id: str,
                                 experiment_id: str):
        try:
            sessions = self._session.prearchive.sessions(
                project=project_id,
                allow_receiving=True,
            )
        except TypeError:
            try:
                sessions = self._session.prearchive.sessions(project=project_id)
            except TypeError:
                sessions = self._session.prearchive.sessions()

        for prearchive_session in sessions:
            session_project = str(getattr(prearchive_session, "project", "") or "").strip()
            if project_id and session_project != project_id:
                continue

            session_subject = str(getattr(prearchive_session, "subject", "") or "").strip()
            if subject_id and session_subject != subject_id:
                continue

            session_keys = {
                str(getattr(prearchive_session, "name", "") or "").strip(),
                str(getattr(prearchive_session, "label", "") or "").strip(),
                str(getattr(prearchive_session, "folder_name", "") or "").strip(),
                str(getattr(prearchive_session, "id", "") or "").strip(),
            }
            if experiment_id and experiment_id not in session_keys:
                continue

            if subject_id or experiment_id:
                return prearchive_session

        return None

    @staticmethod
    def _as_prearchive_session(imported_session):
        if imported_session is None:
            return None

        has_archive = callable(getattr(imported_session, "archive", None))
        has_rebuild = callable(getattr(imported_session, "rebuild", None))
        if has_archive and has_rebuild:
            return imported_session

        return None

    @staticmethod
    def _is_transient_prearchive_status(status: str) -> bool:
        normalized_status = str(status or "").strip().lower()
        transient_statuses = {
            "build pending",
            "building now",
            "archive pending",
            "archiving now",
            "rebuild pending",
            "rebuilding now",
            "delete pending",
            "deleting now",
            "move pending",
            "moving now",
        }
        return normalized_status in transient_statuses

    @staticmethod
    def _is_error_prearchive_status(status: str) -> bool:
        normalized_status = str(status or "").strip().lower()
        error_tokens = {"error", "failed", "fail"}
        return any(token in normalized_status for token in error_tokens)

    def _classify_prearchive_status(self, status: str) -> str:
        normalized_status = str(status or "").strip().lower()
        if not normalized_status:
            return "unknown"
        if self._is_transient_prearchive_status(normalized_status):
            return "transient"
        if self._is_receiving_prearchive_status(normalized_status):
            return "receiving"
        if self._is_archivable_prearchive_status(normalized_status):
            return "archivable"
        if self._is_error_prearchive_status(normalized_status):
            return "error"
        if self._is_in_progress_prearchive_status(normalized_status):
            return "in_progress"
        return "unknown"

    @staticmethod
    def _is_receiving_prearchive_status(status: str) -> bool:
        normalized_status = str(status or "").strip().lower()
        return "receiv" in normalized_status

    @staticmethod
    def _is_archivable_prearchive_status(status: str) -> bool:
        normalized_status = str(status or "").strip().lower()
        return normalized_status in {"ready", "conflict"}

    @staticmethod
    def _is_in_progress_prearchive_status(status: str) -> bool:
        normalized_status = str(status or "").strip().lower()
        in_progress_tokens = {
            "rebuild",
            "build",
            "archiv",
            "process",
            "queue",
            "mov",
        }
        return any(token in normalized_status for token in in_progress_tokens)

    @staticmethod
    def _upload_resource_file(resource, local_file: Path, remote_path: str) -> None:
        """Upload ``local_file`` preserving binary content."""
        upload_method = getattr(resource, "upload", None)
        if callable(upload_method):
            upload_method(str(local_file), remotepath=remote_path, overwrite=True)
            return

        with local_file.open("rb") as file_stream:
            resource.upload_data(
                file_stream.read(),
                remotepath=remote_path,
                overwrite=True,
            )

    @staticmethod
    def _sanitize_label(label: str):
        cleaned = "".join(
            ch if (ch.isalnum() or ch in {"_", "-"}) else "_"
            for ch in label.strip()
        )
        return cleaned or None
