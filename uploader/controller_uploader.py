from pathlib import Path
from contextlib import contextmanager
import flet as ft

from enums.tree_type import TreeType
from enums.uploader_level import UploaderLevel
from shared_ui.ui.xnat_new_subject.controller_xnat_new_subject import ControllerXnatNewSubject
from shared_ui.ui.xnat_new_subject.model_xnat_new_subject import ModelXnatNewSubject
from shared_ui.ui.xnat_new_subject.view_xnat_new_subject import ViewXnatNewSubject
from uploader.services.dicom.dicom_preview_service import DicomPreviewService
from uploader.services.dicom.dicom_tag_reader_service import \
    DicomTagReaderService
from shared_ui.ui.treeview.controller_treeview import ControllerTreeview
from shared_ui.ui.treeview.model_treeview import ModelTreeview
from shared_ui.ui.treeview.view_treeview import ViewTreeview
from xnat_client.xnat_repository import XnatRepository
from shared_ui.ui.xnat_new_project.model_xnat_new_project import \
    ModelXnatNewProject
from shared_ui.ui.xnat_new_project.view_xnat_new_project import ViewXnatNewProject
from shared_ui.ui.xnat_new_project.controller_xnat_new_project import \
    ControllerXnatNewProject
from shared_ui.ui.xnat_new_experiment.model_xnat_new_experiment import \
    ModelXnatNewExperiment
from shared_ui.ui.xnat_new_experiment.view_xnat_new_experiment import \
    ViewXnatNewExperiment
from shared_ui.ui.xnat_new_experiment.controller_xnat_new_experiment import \
    ControllerXnatNewExperiment
from xnat_client.xnat_upload import XnatUpload


class ControllerUploader:
    def __init__(self, view, model):

        self._view = view
        self._model = model

        # Treeview
        self._treeview_model = ModelTreeview()
        self._treeview_view = ViewTreeview(self._view)
        self._treeview_controller = ControllerTreeview(
            self._treeview_model,
            self._treeview_view,
            on_collapse=self._on_treeview_collapse,
            on_expand_selected=self._on_treeview_expand,
            on_file_selected=self._on_treeview_file_selected,
        )

        # Xnat Auth
        self._view_xnat_auth = None
        self._controller_xnat_auth = None

        # New project
        self._model_xnat_new_project = ModelXnatNewProject()
        self._view_xnat_new_project = ViewXnatNewProject(self._view.page)
        self._controller_xnat_new_project = ControllerXnatNewProject(
            self._view_xnat_new_project,
            self._model_xnat_new_project,
            on_submit=self.on_data_project_collected,
        )
        self._view_xnat_new_project.set_controller(
            self._controller_xnat_new_project)

        # New subject
        self._model_xnat_new_subject = ModelXnatNewSubject()
        self._view_xnat_new_subject = ViewXnatNewSubject(self._view.page)
        self._controller_xnat_new_subject = ControllerXnatNewSubject(
            self._view_xnat_new_subject,
            self._model_xnat_new_subject,
            on_submit=self.on_data_subject_collected,
        )
        self._view_xnat_new_subject.set_controller(
            self._controller_xnat_new_subject)

        # New experiment
        self._model_xnat_new_experiment = ModelXnatNewExperiment()
        self._view_xnat_new_experiment = ViewXnatNewExperiment(self._view.page)
        self._controller_xnat_new_experiment = ControllerXnatNewExperiment(
            self._view_xnat_new_experiment,
            self._model_xnat_new_experiment,
            on_submit=self.on_data_experiment_collected,
        )
        self._view_xnat_new_experiment.set_controller(
            self._controller_xnat_new_experiment)
        self._view_xnat_new_experiment.dd_project.on_change = (
            self.on_new_experiment_project_selected
        )

        self._xnat_session = None
        self._xnat_repo = None
        self._xnat_upload = None

        self._selected_file_path = None
        self._selected_folder_path = None
        self._preview_cache = {}

    @property
    def preview_cache(self):
        """Temporary compatibility accessor for the view layer."""
        return self._preview_cache

    # ==========================================================
    # XNAT LOGIN
    # ==========================================================
    def set_xnat_auth(self, view_auth, controller_auth):
        self._view_xnat_auth = view_auth
        self._controller_xnat_auth = controller_auth

    def on_enter_route(self):
        dlg = self._view_xnat_auth.build_dialog(
            on_success=self._on_login_success,
            on_cancel=self._on_login_cancel,
        )
        self._view.open_auth_dialog(dlg)

    def on_exit_route(self):
        self._reset_workflow_state()
        if self._xnat_session:
            self._xnat_session.disconnect()
        self._xnat_session = None
        self._xnat_repo = None
        self._xnat_upload = None

    # ==========================================================
    # HOME / BACK
    # ==========================================================
    def go_home(self):
        self._view.page.go("/")

    def on_home_back_clicked(self, e: ft.ControlEvent):
        if self._model.level is None:
            self._reset_workflow_state()
            self.go_home()
        else:
            self._reset_workflow_state()

    # ==========================================================
    # SET LEVEL (PROJECT / SUBJECT / EXPERIMENT / FILE)
    # ==========================================================
    def upload_project(self, e: ft.ControlEvent):
        """Start a project-level uploader and open the directory picker."""
        self._set_level(UploaderLevel.PROJECT)
        self._view.open_directory_picker()

    def upload_subject(self, e: ft.ControlEvent):
        """Start a subject-level uploader and open the directory picker."""
        self._set_level(UploaderLevel.SUBJECT)
        self._view.open_directory_picker()

    def upload_experiment(self, e: ft.ControlEvent):
        """Start an experiment-level uploader and open the directory picker."""
        self._set_level(UploaderLevel.EXPERIMENT)
        self._view.open_directory_picker()

    def upload_file(self, e: ft.ControlEvent):
        """Start a file-level uploader and open the directory picker."""
        self._set_level(UploaderLevel.FILE)
        self._view.open_directory_picker()

    # ==========================================================
    # DROPDOWN PROJECT / SUBJECT / EXPERIMENT IN XNAT
    # ==========================================================
    def load_projects(self):
        try:
            projects = self._xnat_repo.list_projects()
            self._view.populate_projects(projects)
        except Exception as e:
            self._view.create_alert(f"Cannot load projects: {e}")

    def on_project_selected(self, e: ft.ControlEvent):
        project_id = self._view.dd_xnat_project.value
        self._view.reset_dropdown(self._view.dd_xnat_subject)
        self._view.reset_dropdown(self._view.dd_xnat_experiment)

        if not project_id:
            return

        try:
            subjects = self._xnat_repo.list_subjects(project_id)
            self._view.populate_subjects(subjects)
        except Exception as e:
            self._view.create_alert(f"Cannot load subjects: {e}")

    def on_subject_selected(self, e: ft.ControlEvent):
        project_id = self._view.dd_xnat_project.value
        subject_id = self._view.dd_xnat_subject.value
        self._view.reset_dropdown(self._view.dd_xnat_experiment)

        if not project_id or not subject_id:
            return

        try:
            experiments = self._xnat_repo.list_experiments(project_id,
                                                           subject_id)
            self._view.populate_experiments(experiments)
        except Exception as e:
            self._view.create_alert(f"Cannot load experiments: {e}")

    # -------------------------------------------------------
    # TREEVIEW DICOM FILES
    # -------------------------------------------------------
    def get_directory_to_upload(self, path: str):
        with self._upload_progress_dialog():
            try:
                self._model.input_root = path
                list_dicom_files = self._model.get_dicom_files()
                validation_report = self._model.validate_dicom_files(list_dicom_files)
            except (ValueError, RuntimeError, OSError) as e:
                self._view.create_alert(f"Cannot load the {self._model.level}: {e}")
                self._reset_workflow_state()
                return
            except Exception as e:
                self._view.create_alert(
                    f"Unexpected error while preparing {self._model.level}: {e}"
                )
                self._reset_workflow_state()
                return

            report_message = self._report_dicom_validation(validation_report)
            if report_message:
                self._view.create_alert(report_message)

            try:
                self._treeview_controller.populate_tree(
                    self._model.tmp_folder_to_upload,
                    TreeType.DICOM)
            except (ValueError, RuntimeError, OSError) as e:
                self._view.create_alert(f"Cannot show upload tree: {e}")
                self._reset_workflow_state()

    # ==========================================================
    # SHOW DICOM TAGS
    # ==========================================================
    def on_show_tags_clicked(self, e: ft.ControlEvent):
        if not self._selected_file_path:
            self._view.create_alert("No file selected.")
            return
        try:
            tags = DicomTagReaderService.read_dicom_tags(
                self._selected_file_path)
            self._view.show_dicom_tags_dialog(tags)
        except Exception as e:
            self._view.create_alert(f"Cannot read DICOM tags: {e}")

    # ==========================================================
    # MODIFY MODALITY
    # ==========================================================
    def modify_modality(self, e: ft.ControlEvent):
        self._view.show_modality_dropdown()

    def on_select_modality(self, e: ft.ControlEvent):
        try:
            if self._selected_folder_path is None and self._selected_file_path is None:
                self._view.create_alert("No file selected.")
                return

            selected_item = self._selected_folder_path if (
                self._selected_folder_path is not None
            ) else self._selected_file_path
            with self._upload_progress_dialog():
                self._model.modify_modality(Path(selected_item), e.control.value)
                self._treeview_controller.populate_tree(
                    Path(self._model.tmp_folder_to_upload),
                    TreeType.DICOM
                )
                self._preview_cache.clear()
                self._view.reset_image_preview()
            self._view.create_alert("DICOM modality changed successfully.")
        except (ValueError, RuntimeError, OSError) as err:
            self._view.create_alert(f"Cannot modify DICOM modality: {err}")
        finally:
            self._view.reset_modality_editor()

    # ==========================================================
    # NEW XNAT PROJECT
    # ==========================================================
    def create_new_project(self, e: ft.ControlEvent):
        if not self._xnat_repo:
            self._view.create_alert("You must login to XNAT first.")
            return

        self._controller_xnat_new_project.reset_form()
        self._view_xnat_new_project.open()

    def on_data_project_collected(self, data: dict[str, str]):
        project_id = data.get("project_id")

        if not project_id:
            self._view.create_alert("Cannot create project: invalid project ID.")
            return

        with self._upload_progress_dialog():
            try:
                created_project = self._xnat_repo.create_project(data)

                project_id = created_project["project_id"]
                project_name = created_project["project_name"] or project_id
            except Exception as ex:
                self._clear_xnat_target_selection()
                self._view.create_alert(f"Cannot create project: {ex}")
                return

            try:
                self._clear_xnat_target_selection()

                self._upsert_and_select_dropdown(
                    self._view.dd_xnat_project,
                    project_id,
                    project_name,
                )
            except Exception as ex:
                self._view.create_alert(
                    f"Project created but list refresh failed: {ex}")
                return

        self._view.create_alert(
            f"Project '{project_id}' created successfully."
        )
    # ==========================================================
    # NEW XNAT SUBJECT
    # ==========================================================
    def create_new_subject(self, e: ft.ControlEvent):
        if not self._xnat_repo:
            self._view.create_alert("You must login to XNAT first.")
            return

        self._controller_xnat_new_subject.reset_form()

        try:
            projects = self._xnat_repo.list_projects()
            self._view_xnat_new_subject.populate_project_options(projects)
        except Exception as ex:
            self._view.create_alert(f"Cannot load projects for new subject: {ex}")
            return

        self._view_xnat_new_subject.open()

    def on_data_subject_collected(self, data: dict[str, str]):
        project_id = data.get("parent_project")
        subject_label = data.get("subject_label")

        if not project_id or not subject_label:
            self._view.create_alert("Cannot create subject: select a project and write subject label.")
            return

        with self._upload_progress_dialog():
            try:
                if self._xnat_repo.subject_exists(project_id, subject_label):
                    self._clear_xnat_target_selection()
                    self._view.create_alert(
                        f"Subject '{subject_label}' already exists in project '{project_id}'."
                    )
                    return
            except Exception as ex:
                self._view.create_alert(f"Cannot verify existing subjects: {ex}")
                return
            try:
                created_subject = self._xnat_repo.create_subject(data)
                project_id = created_subject["project_id"]
                subject_id = created_subject["subject_id"]
                subject_label = created_subject["subject_label"] or subject_id
            except Exception as ex:
                self._clear_xnat_target_selection()
                self._view.create_alert(f"Cannot create subject: {ex}")
                return

            try:
                self._clear_xnat_target_selection()

                self._view.dd_xnat_project.value = project_id

                self._upsert_and_select_dropdown(
                    self._view.dd_xnat_subject,
                    subject_id,
                    subject_label,
                )
            except Exception as ex:
                self._view.create_alert(
                    f"Subject created but list refresh failed: {ex}")
                return

        self._view.create_alert(
            f"Subject '{subject_label}' created successfully."
        )

    # ==========================================================
    # NEW XNAT EXPERIMENT
    # ==========================================================
    def create_new_experiment(self, e: ft.ControlEvent):
        if not self._xnat_repo:
            self._view.create_alert("You must login to XNAT first.")
            return

        self._controller_xnat_new_experiment.reset_form()
        try:
            projects = self._xnat_repo.list_projects()
            self._view_xnat_new_experiment.populate_project_options(projects)
        except Exception as ex:
            self._view.create_alert(f"Cannot load projects for new experiment: {ex}")
            return

        self._view_xnat_new_experiment.open()

    def on_new_experiment_project_selected(self, e: ft.ControlEvent):
        project_id = self._view_xnat_new_experiment.dd_project.value
        self._view_xnat_new_experiment.set_subject_options([])
        self._view_xnat_new_experiment.reset_form_subject()


        if not project_id:
            self._controller_xnat_new_experiment.update_submit()
            return

        try:
            subjects = self._xnat_repo.list_subjects(project_id)

            self._view_xnat_new_experiment.populate_subject_options(subjects)
        except Exception as ex:
            self._view.create_alert(
                f"Cannot load subjects for project '{project_id}': {ex}")

        self._controller_xnat_new_experiment.update_submit()

    def on_data_experiment_collected(self, data: dict[str, str]):
        project_id = data.get("parent_project")
        subject_id = data.get("parent_subject")
        experiment_label = data.get("experiment_label")

        if not project_id or not subject_id or not experiment_label:
            self._view.create_alert(
                "Cannot create experiment: invalid project/subject/experiment label."
            )
            return

        with self._upload_progress_dialog():
            try:
                created_experiment = self._xnat_repo.create_experiment(data)

                project_id = created_experiment["project_id"]
                subject_id = created_experiment["subject_id"]
                experiment_id = created_experiment["experiment_id"]
                experiment_label = created_experiment["experiment_label"] or experiment_id
            except Exception as ex:
                self._clear_xnat_target_selection()
                self._view.create_alert(f"Cannot create experiment: {ex}")
                return

            try:
                self._clear_xnat_target_selection()

                self._view.dd_xnat_project.value = project_id

                subjects = self._xnat_repo.list_subjects(project_id)
                self._view.populate_subjects(subjects)
                self._view.dd_xnat_subject.value = subject_id

                self._upsert_and_select_dropdown(
                    self._view.dd_xnat_experiment,
                    experiment_id,
                    experiment_label,
                )
            except Exception as ex:
                self._view.create_alert(
                    f"Experiment created but experiment list refresh failed: {ex}")
                return

        self._view.create_alert(
            f"Experiment '{experiment_label}' created successfully."
        )

    # ==========================================================
    # UPLOAD
    # ==========================================================
    def dicom_and_not_dicom_upload(self, e: ft.ControlEvent):
        if not self._xnat_upload:
            self._view.create_alert("You must login to XNAT first.")
            return

        base_path = self._model.tmp_folder_to_upload
        if not base_path or not base_path.exists():
            self._view.create_alert("Select a folder to upload.")
            return

        project_id = self._view.dd_xnat_project.value
        if not project_id:
            self._view.create_alert("Select a project in XNAT.")
            return

        with self._upload_progress_dialog():
            try:
                self._upload_planner(base_path, project_id)
            except Exception as err:
                self._view.create_alert(f"Upload error: {err}")

    # ==========================================================
    # PRIVATE METHODS
    # ==========================================================
    def _on_login_success(self, xnat_session):
        self._xnat_session = xnat_session
        self._xnat_repo = XnatRepository(xnat_session)
        self._xnat_upload = XnatUpload(xnat_session)
        self._view.set_initial_state()

    def _on_login_cancel(self):
        if self._xnat_session:
            self._xnat_session.disconnect()
        self._xnat_session = None
        self._xnat_repo = None
        self._xnat_upload = None
        self.go_home()

    def _reset_workflow_state(self):
        """Reset uploader workflow state across model, controller, and view."""
        self._model.reset_state()
        self._selected_file_path = None
        self._selected_folder_path = None
        self._preview_cache.clear()
        self._reset_nested_components_state()
        if self._view.dlg_upload:
            self._view.close_progress_bar_dialog()
        self._view.set_initial_state()

    def _reset_nested_components_state(self):
        """Reset reusable nested components without recreating instances."""
        self._treeview_view.reset_selection()
        self._controller_xnat_new_project.reset_form()
        self._controller_xnat_new_subject.reset_form()
        self._controller_xnat_new_experiment.reset_form()

    def _set_level(self, level: UploaderLevel):
        self._model.level = level
        """Set the uploader level and update the view mode."""
        if level == UploaderLevel.PROJECT:
            self._view.set_mode(xnat_subject=False, xnat_experiment=False)
        elif level == UploaderLevel.SUBJECT:
            self._view.set_mode(xnat_subject=True, xnat_experiment=False)
        elif level == UploaderLevel.EXPERIMENT:
            self._view.set_mode(xnat_subject=True, xnat_experiment=False)
        elif level == UploaderLevel.FILE:
            self._view.set_mode(xnat_subject=True, xnat_experiment=True)

        self.load_projects()

    def _on_treeview_collapse(self, node_path, tile):
        """Handle a treeview node collapse."""
        self._selected_folder_path = None
        self._selected_file_path = None

    def _on_treeview_expand(self, node_path, tile):
        """Handle a treeview node expansion."""
        self._selected_folder_path = node_path
        self._selected_file_path = None

    def _on_treeview_file_selected(self, file_path):
        """Handle a file selection in the treeview."""
        self._selected_file_path = file_path
        self._selected_folder_path = None
        self._show_selected_file_preview()

    def _show_selected_file_preview(self):
        if not self._selected_file_path:
            return

        selected_path = Path(self._selected_file_path)
        if selected_path in self._preview_cache:
            self._view.set_image_preview(self._preview_cache[selected_path])
            return

        try:
            if selected_path.suffix.lower() in (".dcm", ".dicom"):
                b64 = DicomPreviewService.dicom_to_base64(str(selected_path))
                self._preview_cache[selected_path] = b64
                self._view.set_image_preview(b64)
        except Exception as e:
            self._view.create_alert(f"Preview failed: {e}")

    def _upsert_and_select_dropdown(
            self,
            dropdown: ft.Dropdown,
            item_id: str,
            item_name: str,
    ) -> None:
        if not self._dropdown_has_option(dropdown, item_id):
            dropdown.options.append(
                ft.dropdown.Option(
                    key=item_id,
                    text=item_name or item_id,
                )
            )

        dropdown.value = item_id
        dropdown.update()

    def _clear_xnat_target_selection(self):
        self._view.dd_xnat_project.value = None
        self._view.reset_dropdown(self._view.dd_xnat_subject)
        self._view.reset_dropdown(self._view.dd_xnat_experiment)
        self._view.update_page()

    def _upload_planner(self, base_path, project_id):
        if self._model.level == UploaderLevel.FILE:
            self._upload_not_dicom_thread(base_path, project_id)
        else:
            self._upload_dicom_thread(project_id)

    def _upload_dicom_thread(self, project_id: str):
        try:
            upload_targets = self._model.build_upload_targets(
                self._view.dd_xnat_subject.value,
                self._view.dd_xnat_experiment.value,
            )
        except ValueError as err:
            self._view.create_alert(str(err))
            return

        if not upload_targets:
            self._view.create_alert("No experiment folders found.")
            return

        failed_uploads = []
        for exp_folder, subject_id, experiment_id in upload_targets:
            try:
                self._xnat_upload.upload_dicom(
                    exp_folder,
                    project_id,
                    subject_id,
                    experiment_id,
                )
            except Exception as err:
                failed_uploads.append(
                    {
                        "exp_folder": str(exp_folder),
                        "subject_id": subject_id,
                        "experiment_id": experiment_id,
                        "error": str(err),
                    }
                )

        self._view.dlg_upload.open = False
        self._view.update_page()
        if not failed_uploads:
            self._view.create_alert("Upload completed successfully!")
            return

        failed_count = len(failed_uploads)
        success_count = len(upload_targets) - failed_count
        failure_summary = "; ".join(
            (
                f"{entry['experiment_id']} (subject {entry['subject_id']}): "
                f"{entry['error']}"
            )
            for entry in failed_uploads[:5]
        )
        if failed_count > 5:
            failure_summary += "; ..."

        self._view.create_alert(
            "Upload with errors "
            f"({success_count} ok, {failed_count} failed). "
            f"Failed uploads: {failure_summary}"
        )

    def _upload_not_dicom_thread(self, base_path: Path, project_id: str):

        try:
            self._model.validate_resource_upload_context(
                self._view.dd_xnat_subject.value,
                self._view.dd_xnat_experiment.value,
            )
        except ValueError as err:
            self._view.create_alert(str(err))
            return

        subject_id = self._view.dd_xnat_subject.value
        experiment_id = self._view.dd_xnat_experiment.value

        try:
            upload_non_dicom_report = self._xnat_upload.upload_files_resources(
                source_folder=base_path,
                project_id=project_id,
                subject_id=subject_id,
                experiment_id=experiment_id,
            )
        except Exception as err:
            self._view.create_alert(
                f"Error during resources upload: {err}"
            )
            return

        report_message = self._report_non_dicom_upload(upload_non_dicom_report)
        self._view.create_alert(report_message)

    @contextmanager
    def _upload_progress_dialog(self):
        self._view.show_progress_bar_dialog()
        try:
            yield
        finally:
            self._view.close_progress_bar_dialog()

    @staticmethod
    def _dropdown_has_option(dropdown: ft.Dropdown, key: str):
        return any(option.key == key for option in dropdown.options)

    @staticmethod
    def _report_dicom_validation(validation_report: dict) -> str:
        message = ""
        if validation_report["failed"]:
            failed_files = validation_report["failed"]
            total_failed = len(failed_files)

            successful = validation_report["copied"] + validation_report["converted"]

            max_errors_to_show = 3
            displayed_errors = failed_files[:max_errors_to_show]

            error_details = "\n".join(
                f"  • {Path(entry['file']).name}\n"
                f"    Reason: {entry['reason']}"
                for entry in displayed_errors
            )

            remaining = total_failed - len(displayed_errors)

            if remaining > 0:
                error_details += f"\n  ... and {remaining} more error(s)."

            message = (
                "DICOM validation completed with errors.\n\n"
                "Summary:\n"
                f"  • Total files: {validation_report['total']}\n"
                f"  • Copied: {validation_report['copied']}\n"
                f"  • Converted: {validation_report['converted']}\n"
                f"  • Failed: {total_failed}\n\n"
                "Failed files:\n"
                f"{error_details}"
            )

        return message

    @staticmethod
    def _report_non_dicom_upload(upload_report: dict) -> str:

        if upload_report["failed"]:
            failed_files = upload_report["failed"]
            total_failed = len(failed_files)

            successful = upload_report["uploaded"]

            max_errors_to_show = 3
            displayed_errors = failed_files[:max_errors_to_show]

            error_details = "\n".join(
                f"  • {Path(entry['file']).name}\n"
                f"    Reason: {entry['reason']}"
                for entry in displayed_errors
            )

            remaining = total_failed - len(displayed_errors)

            if remaining > 0:
                error_details += f"\n  ... and {remaining} more error(s)."

            message = (
                "Non-DICOM resource upload completed with errors.\n\n"
                "Summary:\n"
                f"  • Total files: {upload_report['total']}\n"
                f"  • Uploaded: {successful}\n"
                f"  • Failed: {total_failed}\n\n"
                "Failed files:\n"
                f"{error_details}"
            )

        else:
            message = (
                "Non-DICOM files upload completed successfully.\n\n"
                "Summary:\n"
                f"  • Total files: {upload_report['total']}\n"
                f"  • Uploaded: {upload_report['uploaded']}\n"
                "  • Failed: 0"
            )

        return message

