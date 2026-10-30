import re
import shutil
import tempfile
import time
from pathlib import Path

import unicodedata
from xnat.core import XNATListing
from xnat.prearchive import PrearchiveSession

from xnat_client.xnat_session import XnatSession


class XnatUpload:
    def __init__(self, xnat_session: XnatSession):
        if not xnat_session.session:
            raise ValueError("XNAT session not connected.")
        self._session = xnat_session.session

    def upload_dicom(self,
                     exp_folder: Path,
                     project_id: str,
                     subject_id: str,
                     experiment_id: str,
                     max_attempts: int=9,
                     poll_interval_seconds: int| float=10.0):

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
            source_folder: Path,
            project_id: str,
            subject_id: str,
            experiment_id: str,
            default_resource_label: str="NON_DICOM",
    ) -> dict[str, int | list[dict[str, str]]]:
        if not source_folder.exists() or not source_folder.is_dir():
            raise ValueError("Resource source folder is not valid.")

        if not project_id or not subject_id or not experiment_id:
            raise ValueError(
                "Project, subject, and experiment are required "
                "to upload resources."
            )

        resource_label = self._sanitize_label(source_folder.name or default_resource_label) or default_resource_label

        if not resource_label:
            raise ValueError("Resource label cannot be empty.")

        files_by_resource = self._group_resource_files(source_folder)

        if not files_by_resource:
            raise ValueError("No non-DICOM files found to upload.")

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

        upload_non_dicom_report = {
            "total": len(files_by_resource),
            "uploaded": 0,
            "failed":  []
        }

        for local_file, remote_path in files_by_resource:
            try:
                # Build the XNAT file URI
                uri = f"{resource.uri.rstrip('/')}/files/{remote_path.lstrip('/')}"

                # Upload file
                self._session.upload_file(
                    uri=uri,
                    path=local_file,
                    overwrite=False,
                    retries=3,
                    timeout=300,
                )
                upload_non_dicom_report["uploaded"] += 1
            except Exception as err:
                upload_non_dicom_report["failed"].append({"file": str(local_file.name), "reason": str(err)})

        self._session.clearcache()

        return upload_non_dicom_report

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

    def list_experiments(self, project_id: str, subject_id: str) -> list[dict[str, str]]:
        subj = self._session.projects[project_id].subjects[subject_id]

        return [
            {"id": e.id, "label": e.label or e.id}
            for e in subj.experiments.values()
        ]

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

        max_rebuild_requests = min(max_attempts, 3)
        max_fetch_failures = max(3, min(6, max_attempts))

        rebuild_requests = 0
        archive_requested = False
        fetch_failures = 0

        last_status = "unknown"
        last_state = "unknown"
        last_action_error = None

        session = self._as_prearchive_session(imported_session)

        for attempt in range(1, max_attempts + 1):

            # 1. Success only if the experiment exists.
            if self.experiment_exists(
                    project_id,
                    subject_id,
                    experiment_id,
            ):
                return

            # 2. Retrieve the session.
            if session is None:
                try:
                    session = self._find_prearchive_session(
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
                            "Repeated failures while retrieving "
                            "the Prearchive session. "
                            f"Failures: {fetch_failures}."
                        ) from err

                    session = None

            # 3. Session absent: wait and check again.
            if session is None:
                if attempt < max_attempts:
                    time.sleep(poll_interval_seconds)
                continue

            # 4. Classification of the state.
            last_status = (
                str(getattr(session, "status", "") or "")
                .strip()
                .lower()
            )

            last_state = self._classify_prearchive_status(last_status)

            # 5. State
            if last_state == "receiving":

                if rebuild_requests >= max_rebuild_requests:
                    raise RuntimeError(
                        "Prearchive session remained in RECEIVING "
                        f"after {rebuild_requests} rebuild request(s). "
                        f"Status: {last_status}."
                    ) from last_action_error

                rebuild_requests += 1

                try:
                    session.rebuild(asynchronous=False)

                except Exception as err:
                    last_action_error = err

                finally:
                    self._session.clearcache()
                    session = None

            elif last_state == "ready":

                if not archive_requested:
                    archive_requested = True

                    try:
                        session.archive(
                            overwrite="append",
                            quarantine=False,
                            project=project_id,
                            subject=subject_id,
                            experiment=experiment_id,
                        )

                    except Exception as err:
                        last_action_error = err

                    finally:
                        self._session.clearcache()
                        session = None

                else:
                    session = None

            elif last_state == "conflict":
                raise RuntimeError(
                    "Prearchive session is in CONFLICT state. "
                    "Automatic archiving is not permitted. "
                    f"Project: {project_id}; "
                    f"subject: {subject_id}; "
                    f"experiment: {experiment_id}; "
                    f"status: {last_status}. "
                    "Resolve the conflict or define an explicit "
                    "conflict-handling policy."
                )

            elif last_state == "transient":
                # BUILD / ARCHIVE / MOVE / DELETE in progress.
                # Do not submit any further operations.
                session = None

            elif last_state == "error":
                raise RuntimeError(
                    "Prearchive session is in ERROR state. "
                    f"Project: {project_id}; "
                    f"subject: {subject_id}; "
                    f"experiment: {experiment_id}; "
                    f"status: {last_status}."
                ) from last_action_error

            elif last_state == "unknown":
                raise RuntimeError(
                    "Unrecognized Prearchive status. "
                    "No automatic action will be performed. "
                    f"Project: {project_id}; "
                    f"subject: {subject_id}; "
                    f"experiment: {experiment_id}; "
                    f"status: {last_status or '<empty>'}."
                )

            else:
                raise RuntimeError(
                    f"Unsupported Prearchive state: {last_state!r}"
                )

            # 6. Wait before the next check.
            if attempt < max_attempts:
                time.sleep(poll_interval_seconds)

        # 7. Final verification independent of the Prearchive.
        if self.experiment_exists(
                project_id,
                subject_id,
                experiment_id,
        ):
            return

        raise RuntimeError(
            "Unable to verify successful session archiving. "
            f"Project: {project_id}; "
            f"subject: {subject_id}; "
            f"experiment: {experiment_id}; "
            f"last_status: {last_status or '<empty>'}; "
            f"last_state: {last_state}; "
            f"rebuild_requests: {rebuild_requests}; "
            f"archive_requested: {archive_requested}."
        ) from last_action_error

    def _find_prearchive_session(
            self,
            project_id: str,
            subject_id: str,
            experiment_id: str,
    ) -> PrearchiveSession | None:

        # Validate search parameters
        if not all((project_id, subject_id, experiment_id)):
            return None

        # Retrieve prearchive sessions, supporting different API signatures
        try:
            sessions = self._session.prearchive.sessions(
                project=project_id,
                allow_receiving=True,
            )
        except TypeError:
            try:
                sessions = self._session.prearchive.sessions(
                    project=project_id
                )
            except TypeError:
                sessions = self._session.prearchive.sessions()

        # Filter matching sessions
        matches = []

        for session in sessions:
            session_project = str(
                getattr(session, "project", "") or ""
            ).strip()

            session_subject = str(
                getattr(session, "subject", "") or ""
            ).strip()

            if session_project != project_id:
                continue

            if session_subject != subject_id:
                continue

            session_identifiers = {
                str(getattr(session, attr, "") or "").strip()
                for attr in ("name", "label", "folder_name", "id")
            }

            if experiment_id not in session_identifiers:
                continue

            matches.append(session)

        # Ensure unique match
        if not matches:
            return None

        if len(matches) > 1:
            raise ValueError(
                f"Multiple prearchive sessions found for "
                f"project={project_id}, subject={subject_id}, "
                f"experiment={experiment_id}"
            )

        return matches[0]

    def _get_or_create_experiment_resource(self,
                                           project_id: str,
                                           subject_id: str,
                                           experiment_id: str,
                                           resource_label: str) -> XNATListing:

        experiment = (
            self._session.projects[project_id]
            .subjects[subject_id]
            .experiments[experiment_id]
        )

        if resource_label not in experiment.resources:
            self._session.classes.ResourceCatalog(
                parent=experiment,
                label=resource_label,
            )
            self._session.clearcache()

        return experiment.resources[resource_label]

    @staticmethod
    def _classify_prearchive_status(status: str) -> str:
        normalized = str(status or "").strip().lower()

        transient_statuses = {
            "archive pending",
            "build pending",
            "delete pending",
            "move pending",
            "archiving now",
            "building now",
            "deleting now",
            "moving now",

        }

        if normalized in transient_statuses:
            return "transient"

        if normalized == "receiving":
            return "receiving"

        if normalized == "ready":
            return "ready"

        if normalized == "conflict":
            return "conflict"

        if normalized == "error":
            return "error"

        return "unknown"

    @staticmethod
    def _as_prearchive_session(imported_session: PrearchiveSession) -> PrearchiveSession | None:
        if imported_session is None:
            return None

        has_archive = callable(
            getattr(imported_session, "archive", None)
        )
        has_rebuild = callable(
            getattr(imported_session, "rebuild", None)
        )

        if has_archive and has_rebuild:
            return imported_session

        return None

    @staticmethod
    def _group_resource_files(source_folder: Path) -> list[tuple[Path, str]]:
        grouped_files = []

        for local_file in source_folder.rglob("*"):
            if not local_file.is_file():
                continue

            if local_file.suffix.lower() in {".dcm", ".dicom"}:
                continue

            remote_path = local_file.relative_to(source_folder).as_posix()
            grouped_files.append((local_file, remote_path))

        return grouped_files

    @staticmethod
    def _sanitize_label(label: str) -> str:
        normalized = unicodedata.normalize("NFKD", label.strip())
        normalized = normalized.encode("ascii", "ignore").decode("ascii")
        normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", normalized)
        normalized = normalized.strip("_")

        if not normalized:
            return ""

        if len(normalized) > 64:
            return ""

        return normalized
