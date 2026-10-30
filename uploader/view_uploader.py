import flet as ft

from enums.dicom_modality import DicomModality
from enums.tree_type import TreeType
from shared_ui.ui.shared_view.base_view import BaseView
from shared_ui.ui.shared_view.auth_dialog_mixin import AuthDialogMixin
from shared_ui.ui.shared_view.xnat_dropdown_mixin import XnatDropdownMixin
from shared_ui.ui.button import Button
from shared_ui.ui.header import build_header
from shared_ui.ui.palette import Palette, default_palette
from shared_ui.ui.section_card import build_section_card


class ViewUploader(BaseView, AuthDialogMixin, XnatDropdownMixin):
    def __init__(self, page: ft.Page):
        super().__init__(page)

        # graphical elements
        self.title = None
        # top level
        self.file_picker = None
        self.btn_project = None
        self.btn_subject = None
        self.btn_experiment = None
        self.btn_file = None
        # preview group
        self.tree_view_dcm = None
        self.tree_view_dcm_list = None
        self.img_preview = None
        self.img_preview_card = None
        self.btn_show_tags = None
        self.cnt_modify_modality = None
        self.btn_modify_modality = None
        self.dd_modify_modality = None
        # xnat dropdowns + new buttons
        self.dd_xnat_project = None
        self.dd_xnat_subject = None
        self.dd_xnat_experiment = None
        self.btn_new_project = None
        self.btn_new_subject = None
        self.btn_new_experiment = None
        # button home/back upload
        self.btn_home_back = None
        self.txt_home_back = None
        self.icon_home_back = None
        self.btn_upload = None
        # progressbar
        self.pb_upload = None
        self.txt_pb = None
        self.dlg_upload = None

        # layout
        self._main_layout = None

        # palette
        self.palette = default_palette()

        # map enum → container
        self._tree_map: dict[TreeType, ft.Container] = {}

    def build_interface(self):
        """Create and return the main layout for the uploader view.

        This method instantiates all UI controls, binds events to the
        controller, defines the page layout, and resets the initial state
        so the view is ready for user interaction.
        """
        self._build_controls()
        self._bind_events()
        self._define_layout()
        self.set_initial_state()
        return self._main_layout

    def set_initial_state(self):
        """Reset the UI to the default idle state."""
        # Enable top-level
        self.btn_project.disabled = False
        self.btn_subject.disabled = False
        self.btn_experiment.disabled = False
        self.btn_file.disabled = False

        # Disable the other controls
        for c in [
            self.btn_show_tags,
            self.btn_modify_modality,
            self.dd_xnat_project,
            self.dd_xnat_subject,
            self.dd_xnat_experiment,
            self.btn_new_project,
            self.btn_new_subject,
            self.btn_new_experiment,
            self.btn_upload,
        ]:
            c.disabled = True

        # Reset home/back
        self.set_home_back_state("Home", ft.Icons.HOME, enabled=True)

        # Reset dropdowns
        self.reset_dropdowns([
            self.dd_xnat_project,
            self.dd_xnat_subject,
            self.dd_xnat_experiment,
        ])

        # Reset preview & tree
        if self.tree_view_dcm_list:
            self.tree_view_dcm_list.controls.clear()
        self._controller.preview_cache.clear()
        self.reset_image_preview()

        # Modality dropdown reset
        self.reset_modality_editor()

        self._page.update()

    def set_mode(self, xnat_subject: bool, xnat_experiment: bool):
        """Switch the UI to the active conversion mode."""
        # enable/disable top-level
        for c in [
            self.btn_project,
            self.btn_subject,
            self.btn_experiment,
            self.btn_file,
        ]:
            c.disabled = True

        # show tags/modify modality
        for c in [
            self.btn_show_tags,
            self.btn_modify_modality,
        ]:
            c.disabled = False

        # dropdown project, subject, experiment in xnat
        self.dd_xnat_project.disabled = False
        self.dd_xnat_subject.disabled = not xnat_subject
        self.dd_xnat_experiment.disabled = not xnat_experiment

        # new project, subject, experiment buttons
        self.btn_new_project.disabled = False
        self.btn_new_subject.disabled = not xnat_subject
        self.btn_new_experiment.disabled = not xnat_experiment

        # upload
        self.btn_upload.disabled = False

        # home/back
        self.set_home_back_state("Back", ft.Icons.ARROW_BACK, enabled=True)

        self._page.update()

    def show_modality_dropdown(self):
        """Switch modality editor UI from button to dropdown selector."""
        self.cnt_modify_modality.controls.clear()
        self.cnt_modify_modality.controls.append(self.dd_modify_modality)
        self._page.update()

    def reset_modality_editor(self):
        """Restore modality editor UI to default button state."""
        self.dd_modify_modality.value = None
        self.cnt_modify_modality.controls.clear()
        self.cnt_modify_modality.controls.append(self.btn_modify_modality)
        self._page.update()

    def reset_image_preview(self):
        """Restore the default image preview placeholder."""
        self.img_preview.src_base64 = None
        self.img_preview.src_bytes = None
        self.img_preview.src = None
        self.img_preview.src = "assets/images/ImagePreview.png"

    def set_image_preview(self, b64: str):
        self.img_preview.src_base64 = b64
        self.img_preview.update()

    def show_dicom_tags_dialog(self, tags:list):
        rows = []
        for elem in tags:
            tag_label = elem.get("tag_hex", str(elem["tag"]))
            rows.append(
                ft.Row(
                    controls=[
                        ft.Text(tag_label, width=140),
                        ft.Text(elem["name"], width=260, no_wrap=True),
                        ft.Text(elem["value"], width=760, no_wrap=True),
                    ]
                )
            )

        dlg = ft.AlertDialog(
            title=ft.Text("DICOM Tags"),
            content=ft.Container(
                ft.ListView(rows, expand=True),
                width=1200,
                height=650,
            ),
            actions=[ft.TextButton("Close",
                                   on_click=lambda e: self._page.close(dlg))],
            modal=True,
        )
        self._page.open(dlg)
        self._page.update()

    def update_tree(self, new_widget: ft.ListView, tree_type: TreeType):
        """Replace the list view for the given tree type and refresh the UI."""
        container = self._tree_map.get(tree_type)
        container.content = new_widget
        if tree_type == TreeType.DICOM:
            self.tree_view_dcm_list = new_widget
        self._page.update()

    def show_progress_bar_dialog(self):
        """Display the modal progress dialog."""
        self.pb_upload.value = None
        self._page.open(self.dlg_upload)
        self._page.update()

    def update_progress_bar(self, idx: int, total_scans: int):
        """Update the progress bar value and refresh the page."""
        value = (idx + 1) / total_scans
        self.pb_upload.value = value
        self.txt_pb.value = f"Experiment uploaded: {idx+1}/{total_scans}"
        self._page.update()

    def close_progress_bar_dialog(self):
        """Close the modal progress dialog if it is open."""
        if self.dlg_upload is None:
            return
        self.dlg_upload.open = False
        self._page.update()

    def file_picker_result(self, e: ft.FilePickerResultEvent):
        """
        Handle file picker results and delegate processing to the controller.

        If a folder is selected, the controller is asked to process it;
        otherwise, the user is alerted and the view resets to the initial
        state.
        """
        if e.path:
            self._controller.get_directory_to_upload(e.path)
        else:
            self.create_alert("No folder selected.")
            self.set_initial_state()

    def _build_controls(self):
        """Instantiate and configure all UI controls used by the view."""
        btn_style = Button().create_button_style(self.palette)

        # title
        self.title = build_header(
            title="XNAT-PIC Uploader",
            icon=ft.Icons.CLOUD_UPLOAD,
            palette=self.palette,
        )

        # level buttons: project, subject, experiment, file
        self.btn_project = Button.build_text_button(
            "Upload Project",
            btn_style,
            tooltip="Select the project to upload",
        )
        self.btn_subject = Button.build_text_button(
            "Upload Subject",
            btn_style,
            tooltip="Select the subject to upload",
        )
        self.btn_experiment = Button.build_text_button(
            "Upload Experiment",
            btn_style,
            tooltip="Select the experiment to upload",
        )
        self.btn_file = Button.build_text_button(
            "Upload non-DICOM files",
            btn_style,
            tooltip="Select the folder containing the non-DICOM files to upload",
        )

        # file picker
        self.file_picker = ft.FilePicker()
        self._page.overlay.append(self.file_picker)

        # treeview dicom
        self.tree_view_dcm_list = ft.ListView(
            controls=[],
            expand=True,
            spacing=4,
        )
        dcm_list_container = ft.Container(
            expand=True,
            content=self.tree_view_dcm_list,
        )
        self._map_tree_container(TreeType.DICOM, dcm_list_container)
        self.tree_view_dcm = self._build_tree_panel(
            title="DICOM",
            icon=ft.Icons.DOCUMENT_SCANNER,
            list_container=dcm_list_container,
        )

        # image preview
        self.img_preview = ft.Image(
            src="assets/images/ImagePreview.png",
            width=200,
            height=200,
            fit=ft.ImageFit.CONTAIN,
            border_radius=12,
        )
        self.img_preview_card = ft.Container(
            width=200,
            height=200,
            padding=12,
            border_radius=18,
            bgcolor=self.palette.surface,
            border=ft.border.all(2, self.palette.primary),
            content=ft.Column(
                spacing=8,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Row(
                        spacing=6,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Icon(
                                ft.Icons.IMAGE_OUTLINED,
                                size=16,
                                color=self.palette.primary,
                            ),
                            ft.Text(
                                "Preview",
                                size=12,
                                weight=ft.FontWeight.W_600,
                                color=self.palette.subtle_text,
                                font_family="Inter",
                            ),
                        ],
                    ),
                    ft.Container(
                        expand=True,
                        alignment=ft.alignment.center,
                        bgcolor=self.palette.surface_stronger,
                        border_radius=14,
                        border=ft.border.all(1, self.palette.surface_stronger),
                        padding=8,
                        content=self.img_preview,
                    ),
                ],
            ),
        )

        # button show dicom tags
        self.btn_show_tags = ft.ElevatedButton(
            content=ft.Row(
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=10,
                controls=[
                    ft.Icon(ft.Icons.LIST_ALT),
                    ft.Text(value="Show DICOM tags",
                            size=16,
                            weight=ft.FontWeight.W_600,
                            font_family="Inter"),
                ],
            ),
            style=btn_style,
            tooltip="Show the DICOM tags of the selected image",
            width=250,
        )
        # button and dropdown modify modality
        self.cnt_modify_modality = ft.Column()
        self.btn_modify_modality = ft.ElevatedButton(
            content=ft.Row(
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=10,
                controls=[
                    ft.Icon(ft.Icons.BUILD),
                    ft.Text(value="Modify DICOM modality",
                            size=16,
                            weight=ft.FontWeight.W_600,
                            font_family="Inter"),
                ],
            ),
            style=btn_style,
            tooltip="Edit the modality tag of the selected file",
            width=250,
        )
        self.dd_modify_modality = ft.Dropdown(
            options=[ft.dropdown.Option(ct.value.upper()) for ct in
                     DicomModality],
            hint_text="New Modality",
            width=200,
            filled=True,
            bgcolor=self.palette.surface,
            border_radius=12,
        )
        self.cnt_modify_modality.controls.append(self.btn_modify_modality)

        # xnat dropdowns project, subject, experiment
        self.dd_xnat_project = ft.Dropdown(
            hint_text="Project",
            prefix_icon=ft.Icons.DASHBOARD,
            expand=True,
            filled=True,
            bgcolor=self.palette.surface,
            border_radius=12,
        )
        self.dd_xnat_subject = ft.Dropdown(
            hint_text="Subject",
            prefix_icon=ft.Icons.PERSON,
            expand=True,
            filled=True,
            bgcolor=self.palette.surface,
            border_radius=12,
        )
        self.dd_xnat_experiment = ft.Dropdown(
            hint_text="Experiment",
            prefix_icon=ft.Icons.SCIENCE,
            expand=True,
            filled=True,
            bgcolor=self.palette.surface,
            border_radius=12,
        )

        # xnat new project, subject, experiment
        self.btn_new_project = Button.build_text_button(
            "New Project",
            btn_style,
            tooltip="Create a new project on XNAT",
        )
        self.btn_new_subject = Button.build_text_button(
            "New Subject",
            btn_style,
            tooltip="Create a new subject on XNAT",
        )
        self.btn_new_experiment = Button.build_text_button(
            "New Experiment",
            btn_style,
            tooltip="Create a new experiment on XNAT",
        )

        # button home/back and upload
        self.txt_home_back = ft.Text(
            value="Go home",
            size=16,
            weight=ft.FontWeight.W_600,
            font_family="Inter",
        )
        self.icon_home_back = ft.Icon(ft.Icons.HOME, size=26)
        self.btn_home_back = Button.build_text_button(
            "Go home",
            btn_style,
            icon=self.icon_home_back,
            text_control=self.txt_home_back,
        )
        self.btn_upload = Button.build_text_button(
            "Upload",
            btn_style,
            icon=ft.Icon(ft.Icons.CLOUD_UPLOAD, size=26),
        )

        # progressbar dialog
        self.pb_upload = ft.ProgressBar(width=320, value=None)
        self.txt_pb = ft.Text("Loading...")
        self.dlg_upload = ft.AlertDialog(
            modal=True,
            title=self.txt_pb,
            content=ft.Column(
                tight=True,
                spacing=12,
                controls=[
                    self.pb_upload,
                ],
            ),
        )

    def _bind_events(self):
        """Bind events"""
        self.btn_project.on_click = self._controller.upload_project
        self.btn_subject.on_click = self._controller.upload_subject
        self.btn_experiment.on_click = self._controller.upload_experiment
        self.btn_file.on_click = self._controller.upload_file
        self.file_picker.on_result = self.file_picker_result
        self.btn_show_tags.on_click = self._controller.on_show_tags_clicked
        self.btn_modify_modality.on_click = self._controller.modify_modality
        self.dd_modify_modality.on_change = self._controller.on_select_modality
        self.dd_xnat_project.on_change = self._controller.on_project_selected
        self.dd_xnat_subject.on_change = self._controller.on_subject_selected
        self.btn_new_project.on_click = self._controller.create_new_project
        self.btn_new_subject.on_click = self._controller.create_new_subject
        self.btn_new_experiment.on_click = self._controller.create_new_experiment
        self.btn_home_back.on_click = self._controller.on_home_back_clicked
        self.btn_upload.on_click = self._controller.dicom_and_not_dicom_upload

    def _define_layout(self):
        """Define layout"""
        header_section = ft.Column(
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
            controls=[self.title],
        )

        row_levels = ft.ResponsiveRow(
            alignment=ft.MainAxisAlignment.CENTER,
            run_spacing=12,
            spacing=12,
            controls=[
                ft.Container(col={"xs": 12, "sm": 6, "md": 3},
                             content=self.btn_project),
                ft.Container(col={"xs": 12, "sm": 6, "md": 3},
                             content=self.btn_subject),
                ft.Container(col={"xs": 12, "sm": 6, "md": 3},
                             content=self.btn_experiment),
                ft.Container(col={"xs": 12, "sm": 6, "md": 3},
                             content=self.btn_file),
            ],
        )

        col_tools = ft.Column(
            [
                self.btn_show_tags,
                self.cnt_modify_modality,
            ],
            spacing=12,
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

        row_file = ft.ResponsiveRow(
            alignment=ft.MainAxisAlignment.CENTER,
            run_spacing=16,
            spacing=16,
            controls=[
                ft.Container(col={"xs": 12, "md": 6},
                             content=self.tree_view_dcm),
                ft.Container(
                    col={"xs": 12, "md": 3},
                    alignment=ft.alignment.center,
                    content=self.img_preview_card,
                ),
                ft.Container(
                    col={"xs": 12, "md": 3},
                    alignment=ft.alignment.center,
                    content=ft.Container(
                        alignment=ft.alignment.center,
                        height=200,
                        content=col_tools,
                    ),
                ),
            ],
        )

        row_dd = ft.ResponsiveRow(
            alignment=ft.MainAxisAlignment.CENTER,
            run_spacing=12,
            spacing=12,
            controls=[
                ft.Container(col={"xs": 12, "md": 4},
                             content=self.dd_xnat_project),
                ft.Container(col={"xs": 12, "md": 4},
                             content=self.dd_xnat_subject),
                ft.Container(col={"xs": 12, "md": 4},
                             content=self.dd_xnat_experiment),
            ],
        )

        row_new = ft.ResponsiveRow(
            alignment=ft.MainAxisAlignment.CENTER,
            run_spacing=12,
            spacing=12,
            controls=[
                ft.Container(col={"xs": 12, "md": 4},
                             content=self.btn_new_project),
                ft.Container(col={"xs": 12, "md": 4},
                             content=self.btn_new_subject),
                ft.Container(col={"xs": 12, "md": 4},
                             content=self.btn_new_experiment),
            ],
        )

        row_home_upload = ft.Row(
            [self.btn_home_back, self.btn_upload],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

        local_section = build_section_card(
            title="Upload from your PC",
            description=(
                "Choose the source level and review the DICOM preview before "
                "sending data to XNAT."
            ),
            icon=ft.Icons.COMPUTER,
            content=ft.Column(
                spacing=16,
                controls=[
                    row_levels,
                    row_file,
                ],
            ),
            palette=self.palette,
        )

        xnat_section = build_section_card(
            title="To XNAT",
            description=(
                "Select the destination project, subject, and experiment or "
                "create new records before uploading."
            ),
            icon=ft.Icons.CLOUD,
            content=ft.Column(
                spacing=16,
                controls=[
                    row_dd,
                    row_new,
                ],
            ),
            palette=self.palette,
        )

        self._main_layout = ft.Container(
            expand=True,
            padding=ft.padding.symmetric(horizontal=28, vertical=18),
            content=ft.Column(
                alignment=ft.MainAxisAlignment.START,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=18,
                controls=[
                    header_section,
                    local_section,
                    xnat_section,
                    ft.Container(expand=True),
                    row_home_upload,
                ],
            ),
        )

    def _build_tree_panel(
            self,
            title: str,
            icon: str,
            list_container: ft.Container,
    ) -> ft.Control:
        return ft.Container(
            padding=12,
            bgcolor=self.palette.surface,
            border_radius=16,
            border=ft.border.all(1, self.palette.surface_stronger),
            height=200,
            content=ft.Column(
                spacing=8,
                controls=[
                    ft.Row(
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Icon(icon, size=18, color=self.palette.primary),
                            ft.Text(
                                title,
                                size=13,
                                weight=ft.FontWeight.W_600,
                                color=self.palette.primary_text,
                                font_family="Inter",
                            ),
                        ],
                    ),
                    ft.Divider(height=1, color=self.palette.surface_stronger),
                    list_container,
                ],
            ),
        )

    def _map_tree_container(self, tree_type: TreeType,
                            container: ft.Container):
        """
        Register the container that hosts a tree ListView for updates.
        """
        self._tree_map[tree_type] = container

    @staticmethod
    def _create_default_palette() -> Palette:
        return default_palette()