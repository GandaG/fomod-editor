#!/usr/bin/env python

# Copyright 2016 Daniel Nunes
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from os import makedirs, listdir
from os.path import expanduser, normpath, basename, join, relpath, isdir, isfile, abspath
from io import BytesIO
from threading import Thread
from queue import Queue
from webbrowser import open_new_tab
from datetime import datetime
from collections import deque
from json import JSONDecodeError
from jsonpickle import encode, decode, set_encoder_options
from lxml.etree import parse, tostring, Comment
from PyQt5.QtWidgets import (QFileDialog, QColorDialog, QMessageBox, QLabel, QHBoxLayout, QCommandLinkButton, QDialog,
                             QFormLayout, QLineEdit, QSpinBox, QComboBox, QWidget, QPushButton, QSizePolicy, QStatusBar,
                             QCompleter, QApplication, QMainWindow, QUndoCommand, QUndoStack, QMenu, QHeaderView,
                             QAction, QVBoxLayout, QGroupBox, QCheckBox, QRadioButton)
from PyQt5.QtGui import QIcon, QPixmap, QColor, QFont, QStandardItemModel, QStandardItem
from PyQt5.QtCore import Qt, pyqtSignal, QStringListModel, QMimeData, QEvent
from PyQt5.uic import loadUi
from requests import get, head, codes, ConnectionError, Timeout
from validator import validate_tree, check_warnings, ValidatorError, ValidationError, WarningError, MissingFolderError
from . import cur_folder, __version__
from .nodes import _NodeElement, NodeComment
from .io import import_, new, export, node_factory, copy_node
from .previews import PreviewDispatcherThread
from .props import PropertyFile, PropertyColour, PropertyFolder, PropertyCombo, PropertyInt, PropertyText, \
    PropertyFlagLabel, PropertyFlagValue, PropertyHTML
from .exceptions import DesignerError
from .ui_templates import window_intro, window_mainframe, window_about, window_settings, window_texteditor, \
    window_plaintexteditor, preview_mo


class IntroWindow(QMainWindow, window_intro.Ui_MainWindow):
    """
    The class for the intro window. Subclassed from QDialog and created in Qt Designer.
    """
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.setWindowIcon(QIcon(join(cur_folder, "resources/window_icon.svg")))
        self.setWindowTitle("FOMOD Designer")
        self.version.setText("Version " + __version__)

        self.settings_dict = read_settings()
        recent_files = self.settings_dict["Recent Files"]
        for path in recent_files:
            if not isdir(path):
                recent_files.remove(path)
                continue
            button = QCommandLinkButton(basename(path), path, self)
            button.setIcon(QIcon(join(cur_folder, "resources/logos/logo_enter.png")))
            button.clicked.connect(lambda _, path_=path: self.open_path(path_))
            self.scroll_layout.addWidget(button)

        if not self.settings_dict["General"]["show_intro"]:
            main_window = MainFrame()
            main_window.move(self.pos())
            main_window.show()
            self.close()
        else:
            self.show()

        self.new_button.clicked.connect(lambda: self.open_path(""))
        self.button_help.clicked.connect(MainFrame.help)
        self.button_about.clicked.connect(lambda _, self_=self: MainFrame.about(self_))

    def open_path(self, path):
        """
        Method used to open a path in the main window - closes the intro window and show the main.

        :param path: The path to open.
        """
        main_window = MainFrame()
        self_center = self.mapToGlobal(self.rect().center())
        main_center = main_window.mapToGlobal(main_window.rect().center())
        main_window.move(self_center - main_center)
        main_window.open(path)
        main_window.show()
        self.close()
        if self.settings_dict["General"]["tutorial_advanced"]:
            main_window.setEnabled(False)
            tutorial = loadUi(join(cur_folder, "resources/templates/tutorial_advanced.ui"))
            tutorial.frame_node.resize(main_window.node_tree_view.size())
            tutorial.frame_node.move(
                main_window.node_tree_view.mapTo(main_window, main_window.node_tree_view.pos())
            )
            tutorial.frame_preview.resize(main_window.tabWidget.size())
            tutorial.frame_preview.move(
                main_window.tabWidget.mapTo(main_window, main_window.tabWidget.pos())
            )
            tutorial.frame_prop.resize(main_window.dockWidgetContents.size())
            tutorial.frame_prop.move(
                main_window.dockWidgetContents.mapTo(main_window, main_window.dockWidgetContents.pos())
            )
            tutorial.frame_child.resize(main_window.dockWidgetContents_3.size())
            tutorial.frame_child.move(
                main_window.dockWidgetContents_3.mapTo(main_window, main_window.dockWidgetContents_3.pos())
            )
            tutorial.button_exit.clicked.connect(lambda: main_window.setEnabled(True))
            tutorial.button_exit.clicked.connect(tutorial.close)
            tutorial.setParent(main_window)
            tutorial.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
            tutorial.setAttribute(Qt.WA_TranslucentBackground)
            main_center = main_window.mapToGlobal(main_window.rect().center())
            tutorial_center = tutorial.mapToGlobal(tutorial.rect().center())
            tutorial.move(main_center - tutorial_center)
            tutorial.setEnabled(True)
            tutorial.exec_()
            self.settings_dict["General"]["tutorial_advanced"] = False

        self.settings_dict["General"]["show_intro"] = not self.check_intro.isChecked()
        self.settings_dict["General"]["show_advanced"] = self.check_advanced.isChecked()
        makedirs(join(expanduser("~"), ".fomod"), exist_ok=True)
        with open(join(expanduser("~"), ".fomod", ".designer"), "w") as configfile:
            set_encoder_options("json", indent=4)
            configfile.write(encode(self.settings_dict))


class MainFrame(QMainWindow, window_mainframe.Ui_MainWindow):
    """
    The class for the main window. Subclassed from QMainWindow and created in Qt Designer.
    """

    #: Signals the xml code has changed.
    xml_code_changed = pyqtSignal([object])

    #: Signals the code preview is updated.
    update_code_preview = pyqtSignal([str])

    #: Signals there is an update available.
    update_check_update_available = pyqtSignal()

    #: Signals the app is up-to-date.
    update_check_up_to_date = pyqtSignal()

    #: Signals a connection timed out.
    update_check_timeout = pyqtSignal()

    #: Signals there was an error with the internet connection.
    update_check_connection_error = pyqtSignal()

    #: Signals a new node has been selected in the node tree.
    select_node = pyqtSignal([object])

    #: Signals the previews need to be updated.
    update_previews = pyqtSignal([object])

    class NodeMimeData(QMimeData):
        def __init__(self):
            super().__init__()
            self._node = None
            self._item = None
            self._original_item = None

        def has_node(self):
            if self._node is None:
                return False
            else:
                return True

        def node(self):
            return self._node

        def set_node(self, node):
            self._node = node

        def has_item(self):
            if self._item is None:
                return False
            else:
                return True

        def item(self):
            return self._item

        def set_item(self, item):
            self._item = item

        def original_item(self):
            return self._original_item

        def set_original_item(self, item):
            self._original_item = item

    class NodeStandardModel(QStandardItemModel):
        def mimeData(self, index_list):
            if not index_list:
                return 0

            mime_data = MainFrame.NodeMimeData()
            new_node = copy_node(self.itemFromIndex(index_list[0]).xml_node)
            mime_data.set_item(new_node.model_item)
            mime_data.set_node(new_node)
            mime_data.set_original_item(self.itemFromIndex(index_list[0]))
            return mime_data

        def canDropMimeData(self, mime_data, drop_action, row, col, parent_index):
            if self.itemFromIndex(parent_index) and mime_data.has_node() and mime_data.has_item() and drop_action == 2:
                if isinstance(self.itemFromIndex(parent_index).xml_node, type(mime_data.node().getparent())):
                    return True
                else:
                    return False
            else:
                return False

        def dropMimeData(self, mime_data, drop_action, row, col, parent_index):
            if not self.canDropMimeData(mime_data, drop_action, row, col, parent_index):
                return False

            parent = self.itemFromIndex(parent_index)
            xml_node = mime_data.node()
            parent.xml_node.remove(mime_data.original_item().xml_node)
            parent.xml_node.append(mime_data.node())
            parent.insertRow(row, xml_node.model_item)
            for row_index in range(0, parent.rowCount()):
                if parent.child(row_index) == mime_data.original_item():
                    continue
                parent.child(row_index).xml_node.user_sort_order = str(parent.child(row_index).row()).zfill(7)
                parent.child(row_index).xml_node.save_metadata()
            return True

        def supportedDragActions(self):
            return Qt.MoveAction

    class LineEditChangeCommand(QUndoCommand):
        def __init__(self, original_text, new_text, current_prop_widgets, widget_index, tree_model, item, select_node):
            super().__init__("Line edit changed.")
            self.original_text = original_text
            self.new_text = new_text
            self.current_prop_widgets = current_prop_widgets
            self.widget_index = widget_index
            self.tree_model = tree_model
            self.item = item
            self.select_node = select_node

        def redo(self):
            self.select_node.emit(self.tree_model.indexFromItem(self.item))
            self.current_prop_widgets[self.widget_index].setText(self.new_text)

        def undo(self):
            self.select_node.emit(self.tree_model.indexFromItem(self.item))
            self.current_prop_widgets[self.widget_index].setText(self.original_text)

    class WidgetLineEditChangeCommand(QUndoCommand):
        def __init__(self, original_text, new_text, current_prop_widgets, widget_index, tree_model, item, select_node):
            super().__init__("Widget with line edit changed.")
            self.original_text = original_text
            self.new_text = new_text
            self.current_prop_widgets = current_prop_widgets
            self.widget_index = widget_index
            self.tree_model = tree_model
            self.item = item
            self.select_node = select_node

        def redo(self):
            self.select_node.emit(self.tree_model.indexFromItem(self.item))
            line_edit = None
            for index in range(self.current_prop_widgets[self.widget_index].layout().count()):
                widget = self.current_prop_widgets[self.widget_index].layout().itemAt(index).widget()
                if isinstance(widget, QLineEdit):
                    line_edit = widget
            line_edit.setText(self.new_text)

        def undo(self):
            self.select_node.emit(self.tree_model.indexFromItem(self.item))
            line_edit = None
            for index in range(self.current_prop_widgets[self.widget_index].layout().count()):
                widget = self.current_prop_widgets[self.widget_index].layout().itemAt(index).widget()
                if isinstance(widget, QLineEdit):
                    line_edit = widget
            line_edit.setText(self.original_text)

    class ComboBoxChangeCommand(QUndoCommand):
        def __init__(self, original_text, new_text, current_prop_widgets, widget_index, tree_model, item, select_node):
            super().__init__("Combo box changed.")
            self.original_text = original_text
            self.new_text = new_text
            self.current_prop_widgets = current_prop_widgets
            self.widget_index = widget_index
            self.tree_model = tree_model
            self.item = item
            self.select_node = select_node

        def redo(self):
            self.select_node.emit(self.tree_model.indexFromItem(self.item))
            self.current_prop_widgets[self.widget_index].setCurrentText(self.new_text)

        def undo(self):
            self.select_node.emit(self.tree_model.indexFromItem(self.item))
            self.current_prop_widgets[self.widget_index].setCurrentText(self.original_text)

    class SpinBoxChangeCommand(QUndoCommand):
        def __init__(self, original_int, new_int, current_prop_widgets, widget_index, tree_model, item, select_node):
            super().__init__("Spin box changed.")
            self.original_int = original_int
            self.new_int = new_int
            self.current_prop_widgets = current_prop_widgets
            self.widget_index = widget_index
            self.tree_model = tree_model
            self.item = item
            self.select_node = select_node

        def redo(self):
            self.select_node.emit(self.tree_model.indexFromItem(self.item))
            self.current_prop_widgets[self.widget_index].setValue(self.new_int)

        def undo(self):
            self.select_node.emit(self.tree_model.indexFromItem(self.item))
            self.current_prop_widgets[self.widget_index].setValue(self.original_int)

    class RunWizardCommand(QUndoCommand):
        def __init__(self, parent_node, original_node, modified_node, tree_model, select_node_signal):
            super().__init__("Wizard was run on this node.")
            self.parent_node = parent_node
            self.original_node = original_node
            self.modified_node = modified_node
            self.tree_model = tree_model
            self.select_node_signal = select_node_signal

        def redo(self):
            self.parent_node.remove_child(self.original_node)
            self.parent_node.add_child(self.modified_node)
            self.parent_node.model_item.sortChildren(0)
            self.select_node_signal.emit(self.tree_model.indexFromItem(self.modified_node.model_item))

        def undo(self):
            self.parent_node.remove_child(self.modified_node)
            self.parent_node.add_child(self.original_node)
            self.parent_node.model_item.sortChildren(0)
            self.select_node_signal.emit(self.tree_model.indexFromItem(self.original_node.model_item))

    class DeleteCommand(QUndoCommand):
        def __init__(self, node_to_delete, tree_model, select_node_signal):
            super().__init__("Node deleted.")
            self.node_to_delete = node_to_delete
            self.parent_node = node_to_delete.getparent()
            self.tree_model = tree_model
            self.select_node_signal = select_node_signal

        def redo(self):
            object_to_delete = self.node_to_delete
            new_index = self.tree_model.indexFromItem(self.parent_node.model_item)
            self.parent_node.remove_child(object_to_delete)
            self.select_node_signal.emit(new_index)

        def undo(self):
            self.parent_node.add_child(self.node_to_delete)
            self.select_node_signal.emit(self.tree_model.indexFromItem(self.node_to_delete.model_item))
            self.tree_model.sort(0)

    class AddChildCommand(QUndoCommand):
        def __init__(self, child_tag, parent_node, tree_model, settings_dict, select_node_signal):
            super().__init__("Child added.")
            self.child_tag = child_tag
            self.parent_node = parent_node
            self.tree_model = tree_model
            self.settings_dict = settings_dict
            self.select_node_signal = select_node_signal
            self.new_child_node = None

        def redo(self):
            if self.new_child_node is None:
                self.new_child_node = node_factory(self.child_tag, self.parent_node)
                defaults_dict = self.settings_dict["Defaults"]
                if self.child_tag in defaults_dict and defaults_dict[self.child_tag].enabled():
                    self.new_child_node.properties[defaults_dict[self.child_tag].key()].set_value(
                        defaults_dict[self.child_tag].value()
                    )
            self.parent_node.add_child(self.new_child_node)
            self.tree_model.sort(0)

            # select the new item
            self.select_node_signal.emit(self.tree_model.indexFromItem(self.new_child_node.model_item))

        def undo(self):
            self.parent_node.remove_child(self.new_child_node)

            # select the parent after removing
            self.select_node_signal.emit(self.tree_model.indexFromItem(self.parent_node.model_item))

    class PasteCommand(QUndoCommand):
        def __init__(self, parent_item, status_bar, tree_model, select_node_signal):
            super().__init__("Node pasted.")
            self.parent_item = parent_item
            self.status_bar = status_bar
            self.tree_model = tree_model
            self.select_node_signal = select_node_signal
            self.pasted_node = None

        def redo(self):
            self.pasted_node = copy_node(QApplication.clipboard().mimeData().node())
            self.parent_item.xml_node.append(self.pasted_node)
            self.parent_item.appendRow(self.pasted_node.model_item)
            self.parent_item.sortChildren(0)

        def undo(self):
            self.parent_item.xml_node.remove_child(self.pasted_node)

            # select the parent after removing
            self.select_node_signal.emit(self.tree_model.indexFromItem(self.parent_item.xml_node.model_item))

    def __init__(self):
        super().__init__()
        self.setupUi(self)

        # setup the icons properly
        self.setWindowIcon(QIcon(join(cur_folder, "resources/window_icon.svg")))
        self.action_Open.setIcon(QIcon(join(cur_folder, "resources/logos/logo_open_file.png")))
        self.action_Save.setIcon(QIcon(join(cur_folder, "resources/logos/logo_floppy_disk.png")))
        self.actionO_ptions.setIcon(QIcon(join(cur_folder, "resources/logos/logo_gear.png")))
        self.action_Refresh.setIcon(QIcon(join(cur_folder, "resources/logos/logo_refresh.png")))
        self.action_Delete.setIcon(QIcon(join(cur_folder, "resources/logos/logo_cross.png")))
        self.action_About.setIcon(QIcon(join(cur_folder, "resources/logos/logo_notepad.png")))
        self.actionHe_lp.setIcon(QIcon(join(cur_folder, "resources/logos/logo_info.png")))
        self.actionCopy.setIcon(QIcon(join(cur_folder, "resources/logos/logo_copy.png")))
        self.actionPaste.setIcon(QIcon(join(cur_folder, "resources/logos/logo_paste.png")))
        self.actionRedo.setIcon(QIcon(join(cur_folder, "resources/logos/logo_redo.png")))
        self.actionUndo.setIcon(QIcon(join(cur_folder, "resources/logos/logo_undo.png")))
        self.actionClear.setIcon(QIcon(join(cur_folder, "resources/logos/logo_clear.png")))
        self.menu_Recent_Files.setIcon(QIcon(join(cur_folder, "resources/logos/logo_recent.png")))
        self.actionExpand_All.setIcon(QIcon(join(cur_folder, "resources/logos/logo_expand.png")))
        self.actionCollapse_All.setIcon(QIcon(join(cur_folder, "resources/logos/logo_collapse.png")))
        self.actionHide_Node.setIcon(QIcon(join(cur_folder, "resources/logos/logo_hide.png")))
        self.actionShow_Node.setIcon(QIcon(join(cur_folder, "resources/logos/logo_show.png")))

        # manage undo and redo
        self.undo_stack = QUndoStack(self)
        self.undo_stack.setUndoLimit(25)
        self.undo_stack.canRedoChanged.connect(self.actionRedo.setEnabled)
        self.undo_stack.canUndoChanged.connect(self.actionUndo.setEnabled)
        self.actionRedo.triggered.connect(self.undo_stack.redo)
        self.actionUndo.triggered.connect(self.undo_stack.undo)

        # manage the node tree view
        self.node_tree_view.clicked.connect(self.select_node.emit)
        self.node_tree_view.activated.connect(self.select_node.emit)
        self.node_tree_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.node_tree_view.customContextMenuRequested.connect(self.on_custom_context_menu)

        # manage node tree model
        self.node_tree_model = self.NodeStandardModel()
        self.node_tree_view.setModel(self.node_tree_model)
        self.node_tree_model.itemChanged.connect(lambda item: item.xml_node.save_metadata())
        self.node_tree_model.itemChanged.connect(lambda item: self.xml_code_changed.emit(item.xml_node))

        # connect actions to the respective methods
        self.action_Open.triggered.connect(self.open)
        self.action_Save.triggered.connect(self.save)
        self.actionO_ptions.triggered.connect(self.settings)
        self.action_Refresh.triggered.connect(self.refresh)
        self.action_Delete.triggered.connect(self.delete)
        self.actionHide_Node.triggered.connect(self.hide_node)
        self.actionShow_Node.triggered.connect(self.show_node)
        self.actionHe_lp.triggered.connect(self.help)
        self.action_About.triggered.connect(lambda _, self_=self: self.about(self_))
        self.actionClear.triggered.connect(self.clear_recent_files)
        self.actionCopy.triggered.connect(
            lambda: self.copy_item_to_clipboard()
            if self.node_tree_view.selectedIndexes() else None
        )
        self.actionPaste.triggered.connect(
            lambda: self.paste_item_from_clipboard()
            if self.node_tree_view.selectedIndexes() else None
        )
        self.actionExpand_All.triggered.connect(self.node_tree_view.expandAll)
        self.actionCollapse_All.triggered.connect(self.node_tree_view.collapseAll)
        self.action_Object_Tree.toggled.connect(self.node_tree.setVisible)
        self.actionObject_Box.toggled.connect(self.children_box.setVisible)
        self.action_Property_Editor.toggled.connect(self.property_editor.setVisible)
        self.node_tree.visibilityChanged.connect(self.action_Object_Tree.setChecked)
        self.children_box.visibilityChanged.connect(self.actionObject_Box.setChecked)
        self.property_editor.visibilityChanged.connect(self.action_Property_Editor.setChecked)

        # setup any necessary variables
        self.original_title = self.windowTitle()
        self._package_path = ""
        self.package_name = ""
        self.settings_dict = read_settings()
        self._info_root = None
        self._config_root = None
        self._current_prop_list = []
        self.original_prop_value_list = {}

        # start the preview threads
        self.preview_queue = Queue()
        self.preview_gui_worker = PreviewMoGui(self.layout_mo)
        self.update_previews.connect(self.preview_queue.put)
        self.update_code_preview.connect(self.xml_code_browser.setHtml)
        self.preview_thread = PreviewDispatcherThread(
            self.preview_queue,
            self.update_code_preview,
            **{
                "package_path": self.package_path,
                "info_root": self.info_root,
                "config_root": self.config_root,
                "gui_worker": self.preview_gui_worker
            }
        )
        self.preview_thread.start()

        # manage the wizard button
        self.button_wizard.clicked.connect(self.run_wizard)

        # manage auto-completion
        self.flag_label_model = QStringListModel()
        self.flag_label_completer = QCompleter()
        self.flag_label_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.flag_label_completer.setModel(self.flag_label_model)
        self.flag_value_model = QStringListModel()
        self.flag_value_completer = QCompleter()
        self.flag_value_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.flag_value_completer.setModel(self.flag_value_model)

        # connect node selected signal
        self.current_node = None  # type: _NodeElement
        self.select_node.connect(
            lambda index: self.set_current_node(self.node_tree_model.itemFromIndex(index).xml_node)
        )
        self.select_node.connect(lambda index: self.node_tree_view.setCurrentIndex(index))
        self.select_node.connect(
            lambda: self.update_previews.emit(self.current_node)
            if self.settings_dict["General"]["code_refresh"] >= 2 else None
        )
        self.select_node.connect(self.update_children_box)
        self.select_node.connect(self.update_props_list)
        self.select_node.connect(lambda: self.action_Delete.setEnabled(True))
        self.select_node.connect(
            lambda: self.button_wizard.setEnabled(False)
            if self.current_node.wizard is None else self.button_wizard.setEnabled(True)
        )
        self.select_node.connect(
            lambda index: self.actionHide_Node.setEnabled(True)
            if self.current_node is not self._config_root and
            self.current_node is not self._info_root and
            self.current_node not in self.current_node.getparent().hidden_children and
            not self.current_node.allowed_instances
            else self.actionHide_Node.setEnabled(False)
        )
        self.select_node.connect(
            lambda index: self.actionShow_Node.setEnabled(True)
            if self.current_node is not self._config_root and
            self.current_node is not self._info_root and
            self.current_node in self.current_node.getparent().hidden_children and
            not self.current_node.allowed_instances
            else self.actionShow_Node.setEnabled(False)
        )

        # manage code changed signal
        self.xml_code_changed.connect(self.update_previews.emit)

        # manage clean/dirty states
        self.undo_stack.cleanChanged.connect(
            lambda clean: self.setWindowTitle(self.package_name + " - " + self.original_title)
            if clean
            else self.setWindowTitle("*" + self.package_name + " - " + self.original_title)
        )
        self.undo_stack.cleanChanged.connect(
            lambda clean: self.action_Save.setEnabled(not clean)
        )

        self.update_recent_files()
        self.check_updates()

        # disable the wizards until they're up-to-date
        self.button_wizard.hide()

    def on_custom_context_menu(self, position):
        index = self.node_tree_view.indexAt(position)
        node_tree_context_menu = QMenu(self.node_tree_view)
        node_tree_context_menu.addActions([self.actionExpand_All, self.actionCollapse_All])

        if index.isValid():
            self.select_node.emit(index)
            node_tree_context_menu.addSeparator()
            node_tree_context_menu.addAction(self.action_Delete)
            if self.current_node is not self._config_root and self.current_node is not self._info_root:
                if self.current_node in self.current_node.getparent().hidden_children:
                    node_tree_context_menu.addAction(self.actionShow_Node)
                else:
                    node_tree_context_menu.addAction(self.actionHide_Node)
            node_tree_context_menu.addSeparator()
            node_tree_context_menu.addActions([self.actionCopy, self.actionPaste])
            node_tree_context_menu.addSeparator()
            node_tree_context_menu.addActions([self.actionUndo, self.actionRedo])

        node_tree_context_menu.move(self.node_tree_view.mapToGlobal(position))
        node_tree_context_menu.exec_()

    def set_current_node(self, selected_node):
        self.current_node = selected_node

    @property
    def current_prop_list(self):
        return self._current_prop_list

    def info_root(self):
        return self._info_root

    def config_root(self):
        return self._config_root

    def package_path(self):
        return self._package_path

    def copy_item_to_clipboard(self):
        item = self.node_tree_model.itemFromIndex(self.node_tree_view.selectedIndexes()[0])
        QApplication.clipboard().setMimeData(self.node_tree_model.mimeData([self.node_tree_model.indexFromItem(item)]))
        self.actionPaste.setEnabled(True)

    def paste_item_from_clipboard(self):
        parent_item = self.node_tree_model.itemFromIndex(self.node_tree_view.selectedIndexes()[0])
        new_node = copy_node(QApplication.clipboard().mimeData().node())
        if not parent_item.xml_node.can_add_child(new_node):
            self.statusBar().showMessage("This parent is not valid!")
        else:
            self.undo_stack.push(
                self.PasteCommand(
                    parent_item,
                    self.statusBar(),
                    self.node_tree_model,
                    self.select_node
                )
            )

    @staticmethod
    def update_flag_label_completer(label_model, elem_root):
        label_list = []
        for elem in elem_root.iter():
            if elem.tag == "flag":
                value = elem.properties["name"].value
                if value not in label_list:
                    label_list.append(value)
        label_model.setStringList(label_list)

    @staticmethod
    def update_flag_value_completer(value_model, elem_root, label):
        value_list = []
        for elem in elem_root.iter():
            if elem.tag == "flag" and elem.text not in value_list and elem.properties["name"].value == label:
                value_list.append(elem.text)
        value_model.setStringList(value_list)

    def check_updates(self):
        """
        Checks the version number on the remote repository (Github Releases)
        and compares it against the current version.

        If the remote version is higher, then the user is warned in the status bar and advised to get the new one.
        Otherwise, ignore.
        """

        def update_available_button():
            update_button = QPushButton("New Version Available!")
            update_button.setFlat(True)
            update_button.clicked.connect(lambda: open_new_tab("https://github.com/GandaG/fomod-designer/releases/latest"))
            self.statusBar().addPermanentWidget(update_button)

        def check_remote():
            try:
                response = get("https://api.github.com/repos/GandaG/fomod-designer/releases", timeout=10)
                if response.status_code == codes.ok and response.json()[0]["tag_name"][1:] > __version__:
                    self.update_check_update_available.emit()
                else:
                    self.update_check_up_to_date.emit()
            except Timeout:
                self.update_check_timeout.emit()
            except ConnectionError:
                self.update_check_connection_error.emit()

        self.update_check_up_to_date.connect(lambda: self.setStatusBar(QStatusBar()))
        self.update_check_up_to_date.connect(
            lambda: self.statusBar().addPermanentWidget(QLabel("Everything is up-to-date."))
        )
        self.update_check_update_available.connect(lambda: self.setStatusBar(QStatusBar()))
        self.update_check_update_available.connect(update_available_button)
        self.update_check_timeout.connect(lambda: self.setStatusBar(QStatusBar()))
        self.update_check_timeout.connect(lambda: self.statusBar().addPermanentWidget(QLabel("Connection timed out.")))
        self.update_check_connection_error.connect(lambda: self.setStatusBar(QStatusBar()))
        self.update_check_connection_error.connect(
            lambda: self.statusBar().addPermanentWidget(QLabel(
                "Could not connect to remote server, check your internet connection."
            ))
        )

        self.statusBar().addPermanentWidget(QLabel("Checking for updates..."))

        Thread(target=check_remote).start()

    def hide_node(self):
        if self.current_node is not None:
            self.current_node.set_hidden(True)

    def show_node(self):
        if self.current_node is not None:
            self.current_node.set_hidden(False)

    def open(self, path=""):
        """
        Open a new installer if one exists at path (if no path is given a dialog pops up asking the user to choose one)
        or create a new one.

        If enabled in the Settings the installer is also validated and checked for common errors.

        :param path: Optional. The path to open/create an installer at.
        """
        try:
            answer = self.check_fomod_state()
            if answer == QMessageBox.Save:
                self.save()
            elif answer == QMessageBox.Cancel:
                return
            else:
                pass

            if not path:
                open_dialog = QFileDialog()
                package_path = open_dialog.getExistingDirectory(self, "Select package root directory:", expanduser("~"))
            else:
                package_path = path

            if package_path:
                info_root, config_root = import_(normpath(package_path))
                if info_root is not None and config_root is not None:
                    if self.settings_dict["Load"]["validate"]:
                        try:
                            validate_tree(
                                parse(BytesIO(tostring(config_root, pretty_print=True))),
                                join(cur_folder, "resources", "mod_schema.xsd"),
                            )
                        except ValidationError as p:
                            generic_errorbox(p.title, str(p), p.detailed).exec_()
                            if not self.settings_dict["Load"]["validate_ignore"]:
                                return
                    if self.settings_dict["Load"]["warnings"]:
                        try:
                            check_warnings(
                                package_path,
                                config_root,
                            )
                        except WarningError as p:
                            generic_errorbox(p.title, str(p), p.detailed).exec_()
                            if not self.settings_dict["Save"]["warn_ignore"]:
                                return
                else:
                    info_root, config_root = new()

                self._package_path = package_path
                self._info_root, self._config_root = info_root, config_root

                self.node_tree_model.clear()

                self.node_tree_model.appendRow(self._info_root.model_item)
                self.node_tree_model.appendRow(self._config_root.model_item)

                self.package_name = basename(normpath(self._package_path))
                self.current_node = None
                self.xml_code_changed.emit(self.current_node)
                self.undo_stack.setClean()
                self.undo_stack.cleanChanged.emit(True)
                self.undo_stack.clear()
                QApplication.clipboard().clear()
                self.actionPaste.setEnabled(False)
                self.action_Delete.setEnabled(False)
                self.update_recent_files(self._package_path)
                self.clear_prop_list()
                self.button_wizard.setEnabled(False)
        except (DesignerError, ValidatorError) as p:
            generic_errorbox(p.title, str(p), p.detailed).exec_()
            return

    def save(self):
        """
        Saves the current installer at the current path.

        If enabled in the Settings the installer is also validated and checked for common errors.
        """
        try:
            if self._info_root is None and self._config_root is None:
                return
            elif not self.undo_stack.isClean():
                self._info_root.sort()
                self._config_root.sort()
                if self.settings_dict["Save"]["validate"]:
                    try:
                        validate_tree(
                            parse(BytesIO(tostring(self._config_root, pretty_print=True))),
                            join(cur_folder, "resources", "mod_schema.xsd"),
                        )
                    except ValidationError as e:
                        generic_errorbox(e.title, str(e), e.detailed).exec_()
                        if not self.settings_dict["Save"]["validate_ignore"]:
                            return
                if self.settings_dict["Save"]["warnings"]:
                    try:
                        check_warnings(
                            self._package_path,
                            self._config_root,
                        )
                    except MissingFolderError:
                        pass
                    except WarningError as e:
                        generic_errorbox(e.title, str(e), e.detailed).exec_()
                        if not self.settings_dict["Save"]["warn_ignore"]:
                            return
                export(self._info_root, self._config_root, self._package_path)
                self.undo_stack.setClean()
        except (DesignerError, ValidatorError) as e:
            generic_errorbox(e.title, str(e), e.detailed).exec_()
            return

    def settings(self):
        """
        Opens the Settings dialog.
        """
        config = SettingsDialog(self)
        config.exec_()
        self.settings_dict = read_settings()

    def refresh(self):
        """
        Refreshes all the previews if the refresh rate in Settings is high enough.
        """
        if self.settings_dict["General"]["code_refresh"] >= 1:
            self.update_previews.emit(self.current_node)

    def delete(self):
        """
        Deletes the current node in the tree. No effect when using the Basic View.
        """
        if self.current_node is None:
            self.statusBar().showMessage("Can't delete nothing.")
        elif self.current_node.getparent() is None:
            self.statusBar().showMessage("Can't delete root nodes.")
        else:
            if self.current_node.is_hidden:
                self.current_node.set_hidden(False)
            self.undo_stack.push(self.DeleteCommand(
                self.current_node,
                self.node_tree_model,
                self.select_node
            ))

    @staticmethod
    def help():
        docs_url = "http://fomod-designer.readthedocs.io/en/stable/index.html"
        local_docs = "file://" + abspath(join(cur_folder, "resources", "docs", "index.html"))
        try:
            if head(docs_url, timeout=0.5).status_code == codes.ok:
                open_new_tab(docs_url)
            else:
                raise ConnectionError()
        except (Timeout, ConnectionError):
            open_new_tab(local_docs)

    @staticmethod
    def about(parent):
        """
        Opens the About dialog. This method is static to be able to be called from the Intro window.

        :param parent: The parent of the dialog.
        """
        about_dialog = About(parent)
        about_dialog.exec_()

    def clear_recent_files(self):
        """
        Clears the Recent Files gui menu and settings.
        """
        self.settings_dict["Recent Files"].clear()
        makedirs(join(expanduser("~"), ".fomod"), exist_ok=True)
        with open(join(expanduser("~"), ".fomod", ".designer"), "w") as configfile:
            set_encoder_options("json", indent=4)
            configfile.write(encode(self.settings_dict))

        for child in self.menu_Recent_Files.actions():
            if child is not self.actionClear:
                self.menu_Recent_Files.removeAction(child)
                del child

    def update_recent_files(self, add_new=None):
        """
        Updates the Recent Files gui menu and settings. If called when opening an installer, pass that installer as
        add_new so it can be added to list or placed at the top.

        :param add_new: If a new installer is being opened, add it to the list or move it to the top.
        """
        file_list = deque(self.settings_dict["Recent Files"], maxlen=5)
        self.clear_recent_files()

        # check for invalid paths and remove them
        for path in file_list:
            if not isdir(path):
                file_list.remove(path)

        # check if the path is new or if it already exists - delete the last one or reorder respectively
        if add_new:
            if add_new in file_list:
                file_list.remove(add_new)
            file_list.appendleft(add_new)

        # write the new list to the settings file
        self.settings_dict["Recent Files"] = file_list
        makedirs(join(expanduser("~"), ".fomod"), exist_ok=True)
        with open(join(expanduser("~"), ".fomod", ".designer"), "w") as configfile:
            set_encoder_options("json", indent=4)
            configfile.write(encode(self.settings_dict))

        # populate the gui menu with the new files list
        self.menu_Recent_Files.removeAction(self.actionClear)
        for path in self.settings_dict["Recent Files"]:
            action = self.menu_Recent_Files.addAction(path)
            action.triggered.connect(lambda _, path_=path: self.open(path_))
        self.menu_Recent_Files.addSeparator()
        self.menu_Recent_Files.addAction(self.actionClear)

    def update_children_box(self):
        """
        Updates the possible children to add in Object Box.
        """
        spacer = self.layout_box.takeAt(self.layout_box.count() - 1)
        for index in reversed(range(self.layout_box.count())):
            widget = self.layout_box.takeAt(index).widget()
            if widget is not None:
                widget.deleteLater()

        children_list = list(self.current_node.allowed_children)

        if self.current_node.tag is not Comment:
            children_list.insert(0, NodeComment)

        for child in children_list:
            new_object = child()
            child_button = QPushButton(new_object.name)
            font_button = QFont()
            font_button.setPointSize(8)
            child_button.setFont(font_button)
            child_button.setMaximumSize(5000, 30)
            child_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            child_button.setStatusTip("A possible child node.")
            child_button.clicked.connect(
                lambda _,
                tag_=new_object.tag,
                parent_node=self.current_node,
                tree_model=self.node_tree_model,
                settings_dict=self.settings_dict,
                : self.undo_stack.push(self.AddChildCommand(
                    tag_,
                    parent_node,
                    tree_model,
                    settings_dict,
                    self.select_node
                ))
            )
            if not self.current_node.can_add_child(new_object):
                child_button.setEnabled(False)
            if child in self.current_node.required_children:
                child_button.setStyleSheet(
                    "background-color: " + QColor(self.settings_dict["Appearance"]["required_colour"]).name()
                )
                child_button.setStatusTip(
                    "A button of this colour indicates that at least one of this node is required."
                )
            if child in self.current_node.either_children_group:
                child_button.setStyleSheet(
                    "background-color: " + QColor(self.settings_dict["Appearance"]["either_colour"]).name()
                )
                child_button.setStatusTip(
                    "A button of this colour indicates that only one of these buttons must be used."
                )
            if child in self.current_node.at_least_one_children_group:
                child_button.setStyleSheet(
                    "background-color: " + QColor(self.settings_dict["Appearance"]["atleastone_colour"]).name()
                )
                child_button.setStatusTip(
                    "A button of this colour indicates that from all of these buttons, at least one is required."
                )
            self.layout_box.addWidget(child_button)
        self.layout_box.addSpacerItem(spacer)

    def clear_prop_list(self):
        """
        Deletes all the properties from the Property Editor
        """
        self._current_prop_list.clear()
        for index in reversed(range(self.layout_prop_editor.count())):
            widget = self.layout_prop_editor.takeAt(index).widget()
            if widget is not None:
                widget.deleteLater()

    def update_props_list(self):
        """
        Updates the Property Editor's prop list. Deletes everything and
        then creates the list from the node's properties.
        """
        self.clear_prop_list()

        prop_index = 0
        og_values = self.original_prop_value_list
        prop_list = self._current_prop_list
        props = self.current_node.properties

        for key in props:
            if not props[key].editable:
                continue

            label = QLabel(self.dockWidgetContents)
            label.setObjectName("label_" + str(prop_index))
            label.setText(props[key].name)
            self.layout_prop_editor.setWidget(prop_index, QFormLayout.LabelRole, label)

            if type(props[key]) is PropertyText:
                def open_plain_editor(line_edit_, node):
                    dialog_ui = window_plaintexteditor.Ui_Dialog()
                    dialog = QDialog(self)
                    dialog_ui.setupUi(dialog)
                    dialog_ui.edit_text.setPlainText(line_edit_.text())
                    if node.tag is Comment:
                        for sequence in node.forbidden_sequences:
                            dialog_ui.edit_text.textChanged.connect(
                                lambda: dialog_ui.edit_text.setText(
                                    dialog_ui.edit_text.toPlainText().replace(sequence, "")
                                ) if sequence in dialog_ui.edit_text.toPlainText() else None
                            )
                    dialog_ui.buttonBox.accepted.connect(dialog.close)
                    dialog_ui.buttonBox.accepted.connect(lambda: line_edit_.setText(dialog_ui.edit_text.toPlainText()))
                    dialog_ui.buttonBox.accepted.connect(line_edit_.editingFinished.emit)
                    dialog.exec_()

                og_values[prop_index] = props[key].value
                prop_list.append(QWidget(self.dockWidgetContents))
                layout = QHBoxLayout(prop_list[prop_index])
                text_edit = QLineEdit(prop_list[prop_index])
                text_button = QPushButton(prop_list[prop_index])
                text_button.setText("...")
                text_button.setMaximumWidth(30)
                layout.addWidget(text_edit)
                layout.addWidget(text_button)
                layout.setContentsMargins(0, 0, 0, 0)
                text_edit.setText(props[key].value)
                if self.current_node.tag is Comment:
                    for sequence in self.current_node.forbidden_sequences:
                        text_edit.textChanged.connect(
                            lambda: text_edit.setText(
                                text_edit.text().replace(sequence, "")
                            ) if sequence in text_edit.text() else None
                        )
                text_edit.textChanged.connect(props[key].set_value)
                text_edit.textChanged[str].connect(self.current_node.write_attribs)
                text_edit.textChanged[str].connect(self.current_node.update_item_name)
                text_edit.textChanged[str].connect(
                    lambda: self.xml_code_changed.emit(self.current_node)
                    if self.settings_dict["General"]["code_refresh"] >= 3 else None
                )
                text_edit.editingFinished.connect(
                    lambda index=prop_index: self.undo_stack.push(
                        self.WidgetLineEditChangeCommand(
                            og_values[index],
                            text_edit.text(),
                            self.current_prop_list,
                            index,
                            self.node_tree_model,
                            self.current_node.model_item,
                            self.select_node
                        )
                    )
                    if og_values[index] != text_edit.text() else None
                )
                text_edit.editingFinished.connect(
                    lambda index=prop_index: og_values.update({index: text_edit.text()})
                )
                text_button.clicked.connect(
                    lambda _, line_edit_=text_edit, node=self.current_node: open_plain_editor(line_edit_, node)
                )

            if type(props[key]) is PropertyHTML:
                def open_plain_editor(line_edit_):
                    dialog_ui = window_texteditor.Ui_Dialog()
                    dialog = QDialog(self)
                    dialog_ui.setupUi(dialog)

                    dialog_ui.radio_html.toggled.connect(dialog_ui.widget_warning.setVisible)
                    dialog_ui.button_colour.clicked.connect(
                        lambda: dialog_ui.edit_text.setTextColor(QColorDialog.getColor())
                    )
                    dialog_ui.button_bold.clicked.connect(
                        lambda: dialog_ui.edit_text.setFontWeight(QFont.Bold)
                        if dialog_ui.edit_text.fontWeight() == QFont.Normal
                        else dialog_ui.edit_text.setFontWeight(QFont.Normal)
                    )
                    dialog_ui.button_italic.clicked.connect(
                        lambda: dialog_ui.edit_text.setFontItalic(not dialog_ui.edit_text.fontItalic())
                    )
                    dialog_ui.button_underline.clicked.connect(
                        lambda: dialog_ui.edit_text.setFontUnderline(not dialog_ui.edit_text.fontUnderline())
                    )
                    dialog_ui.button_align_left.clicked.connect(
                        lambda: dialog_ui.edit_text.setAlignment(Qt.AlignLeft)
                    )
                    dialog_ui.button_align_center.clicked.connect(
                        lambda: dialog_ui.edit_text.setAlignment(Qt.AlignCenter)
                    )
                    dialog_ui.button_align_right.clicked.connect(
                        lambda: dialog_ui.edit_text.setAlignment(Qt.AlignRight)
                    )
                    dialog_ui.button_align_justify.clicked.connect(
                        lambda: dialog_ui.edit_text.setAlignment(Qt.AlignJustify)
                    )
                    dialog_ui.buttonBox.accepted.connect(dialog.close)
                    dialog_ui.buttonBox.accepted.connect(
                        lambda: line_edit_.setText(dialog_ui.edit_text.toPlainText())
                        if dialog_ui.radio_plain.isChecked()
                        else line_edit_.setText(dialog_ui.edit_text.toHtml())
                    )
                    dialog_ui.buttonBox.accepted.connect(line_edit_.editingFinished.emit)

                    dialog_ui.widget_warning.hide()
                    dialog_ui.label_warning.setPixmap(QPixmap(join(cur_folder, "resources/logos/logo_danger.png")))
                    dialog_ui.button_colour.setIcon(QIcon(join(cur_folder, "resources/logos/logo_font_colour.png")))
                    dialog_ui.button_bold.setIcon(QIcon(join(cur_folder, "resources/logos/logo_font_bold.png")))
                    dialog_ui.button_italic.setIcon(QIcon(join(cur_folder, "resources/logos/logo_font_italic.png")))
                    dialog_ui.button_underline.setIcon(QIcon(
                        join(cur_folder, "resources/logos/logo_font_underline.png")
                    ))
                    dialog_ui.button_align_left.setIcon(QIcon(
                        join(cur_folder, "resources/logos/logo_font_align_left.png")
                    ))
                    dialog_ui.button_align_center.setIcon(QIcon(
                        join(cur_folder, "resources/logos/logo_font_align_center.png")
                    ))
                    dialog_ui.button_align_right.setIcon(QIcon(
                        join(cur_folder, "resources/logos/logo_font_align_right.png")
                    ))
                    dialog_ui.button_align_justify.setIcon(QIcon(
                        join(cur_folder, "resources/logos/logo_font_align_justify.png")
                    ))
                    dialog_ui.edit_text.setText(line_edit_.text())
                    dialog.exec_()

                og_values[prop_index] = props[key].value
                prop_list.append(QWidget(self.dockWidgetContents))
                layout = QHBoxLayout(prop_list[prop_index])
                text_edit = QLineEdit(prop_list[prop_index])
                text_button = QPushButton(prop_list[prop_index])
                text_button.setText("...")
                text_button.setMaximumWidth(30)
                layout.addWidget(text_edit)
                layout.addWidget(text_button)
                layout.setContentsMargins(0, 0, 0, 0)
                text_edit.setText(props[key].value)
                text_edit.textChanged.connect(props[key].set_value)
                text_edit.textChanged[str].connect(self.current_node.write_attribs)
                text_edit.textChanged[str].connect(self.current_node.update_item_name)
                text_edit.textChanged[str].connect(
                    lambda: self.xml_code_changed.emit(self.current_node)
                    if self.settings_dict["General"]["code_refresh"] >= 3 else None
                )
                text_edit.editingFinished.connect(
                    lambda index=prop_index: self.undo_stack.push(
                        self.WidgetLineEditChangeCommand(
                            og_values[index],
                            text_edit.text(),
                            self.current_prop_list,
                            index,
                            self.node_tree_model,
                            self.current_node.model_item,
                            self.select_node
                        )
                    )
                    if og_values[index] != text_edit.text() else None
                )
                text_edit.editingFinished.connect(
                    lambda index=prop_index: og_values.update({index: text_edit.text()})
                )
                text_button.clicked.connect(lambda _, line_edit_=text_edit: open_plain_editor(line_edit_))

            if type(props[key]) is PropertyFlagLabel:
                og_values[prop_index] = props[key].value
                prop_list.append(QLineEdit(self.dockWidgetContents))
                self.update_flag_label_completer(self.flag_label_model, self._config_root)
                self.flag_label_completer.activated[str].connect(prop_list[prop_index].setText)
                prop_list[prop_index].setCompleter(self.flag_label_completer)
                prop_list[prop_index].textChanged[str].connect(
                    lambda text: self.update_flag_value_completer(self.flag_value_model, self._config_root, text)
                )
                prop_list[prop_index].setText(props[key].value)
                prop_list[prop_index].textChanged[str].connect(props[key].set_value)
                prop_list[prop_index].textChanged[str].connect(self.current_node.write_attribs)
                prop_list[prop_index].textChanged[str].connect(self.current_node.update_item_name)
                prop_list[prop_index].textChanged[str].connect(
                    lambda: self.xml_code_changed.emit(self.current_node)
                    if self.settings_dict["General"]["code_refresh"] >= 3 else None
                )
                prop_list[prop_index].editingFinished.connect(
                    lambda index=prop_index: self.undo_stack.push(
                        self.LineEditChangeCommand(
                            og_values[index],
                            prop_list[index].text(),
                            self.current_prop_list,
                            index,
                            self.node_tree_model,
                            self.current_node.model_item,
                            self.select_node
                        )
                    )
                    if og_values[index] != prop_list[index].text() else None
                )
                prop_list[prop_index].editingFinished.connect(
                    lambda index=prop_index: og_values.update({index: prop_list[index].text()})
                )

            if type(props[key]) is PropertyFlagValue:
                og_values[prop_index] = props[key].value
                prop_list.append(QLineEdit(self.dockWidgetContents))
                prop_list[prop_index].setCompleter(self.flag_value_completer)
                self.flag_value_completer.activated[str].connect(prop_list[prop_index].setText)
                prop_list[prop_index].setText(props[key].value)
                prop_list[prop_index].textChanged[str].connect(props[key].set_value)
                prop_list[prop_index].textChanged[str].connect(self.current_node.write_attribs)
                prop_list[prop_index].textChanged[str].connect(self.current_node.update_item_name)
                prop_list[prop_index].textChanged[str].connect(
                    lambda: self.xml_code_changed.emit(self.current_node)
                    if self.settings_dict["General"]["code_refresh"] >= 3 else None
                )
                prop_list[prop_index].editingFinished.connect(
                    lambda index=prop_index: self.undo_stack.push(
                        self.LineEditChangeCommand(
                            og_values[index],
                            prop_list[index].text(),
                            self.current_prop_list,
                            index,
                            self.node_tree_model,
                            self.current_node.model_item,
                            self.select_node
                        )
                    )
                    if og_values[index] != prop_list[index].text() else None
                )
                prop_list[prop_index].editingFinished.connect(
                    lambda index=prop_index: og_values.update({index: prop_list[index].text()})
                )

            elif type(props[key]) is PropertyInt:
                og_values[prop_index] = props[key].value
                prop_list.append(QSpinBox(self.dockWidgetContents))
                prop_list[prop_index].setValue(int(props[key].value))
                prop_list[prop_index].setMinimum(props[key].min)
                prop_list[prop_index].setMaximum(props[key].max)
                prop_list[prop_index].valueChanged.connect(props[key].set_value)
                prop_list[prop_index].valueChanged.connect(self.current_node.write_attribs)
                prop_list[prop_index].valueChanged.connect(
                    lambda: self.xml_code_changed.emit(self.current_node)
                    if self.settings_dict["General"]["code_refresh"] >= 3 else None
                )
                prop_list[prop_index].valueChanged.connect(
                    lambda new_value, index=prop_index: self.undo_stack.push(
                        self.SpinBoxChangeCommand(
                            og_values[index],
                            new_value,
                            self.current_prop_list,
                            index,
                            self.node_tree_model,
                            self.current_node.model_item,
                            self.select_node
                        )
                    )
                    if og_values[index] != new_value else None
                )
                prop_list[prop_index].valueChanged.connect(
                    lambda new_value, index=prop_index: og_values.update({index: new_value})
                )

            elif type(props[key]) is PropertyCombo:
                og_values[prop_index] = props[key].value
                prop_list.append(QComboBox(self.dockWidgetContents))
                prop_list[prop_index].insertItems(0, props[key].values)
                prop_list[prop_index].setCurrentIndex(props[key].values.index(props[key].value))
                prop_list[prop_index].currentTextChanged.connect(props[key].set_value)
                prop_list[prop_index].currentTextChanged.connect(self.current_node.write_attribs)
                prop_list[prop_index].currentTextChanged.connect(self.current_node.update_item_name)
                prop_list[prop_index].currentTextChanged.connect(
                    lambda: self.xml_code_changed.emit(self.current_node)
                    if self.settings_dict["General"]["code_refresh"] >= 3 else None
                )
                prop_list[prop_index].activated[str].connect(
                    lambda new_value, index=prop_index: self.undo_stack.push(
                        self.ComboBoxChangeCommand(
                            og_values[index],
                            new_value,
                            self.current_prop_list,
                            index,
                            self.node_tree_model,
                            self.current_node.model_item,
                            self.select_node
                        )
                    )
                )
                prop_list[prop_index].activated[str].connect(
                    lambda new_value, index=prop_index: og_values.update({index: new_value})
                )

            elif type(props[key]) is PropertyFile:
                def button_clicked(line_edit_):
                    open_dialog = QFileDialog()
                    file_path = open_dialog.getOpenFileName(self, "Select File:", self._package_path)
                    if file_path[0]:
                        line_edit.setText(relpath(file_path[0], self._package_path))
                    line_edit_.editingFinished.emit()

                og_values[prop_index] = props[key].value
                prop_list.append(QWidget(self.dockWidgetContents))
                layout = QHBoxLayout(prop_list[prop_index])
                line_edit = QLineEdit(prop_list[prop_index])
                push_button = QPushButton(prop_list[prop_index])
                push_button.setText("...")
                push_button.setMaximumWidth(30)
                layout.addWidget(line_edit)
                layout.addWidget(push_button)
                layout.setContentsMargins(0, 0, 0, 0)
                line_edit.setText(props[key].value)
                line_edit.textChanged.connect(props[key].set_value)
                line_edit.textChanged[str].connect(self.current_node.write_attribs)
                line_edit.textChanged[str].connect(self.current_node.update_item_name)
                line_edit.textChanged[str].connect(
                    lambda: self.xml_code_changed.emit(self.current_node)
                    if self.settings_dict["General"]["code_refresh"] >= 3 else None
                )
                line_edit.editingFinished.connect(
                    lambda index=prop_index: self.undo_stack.push(
                        self.WidgetLineEditChangeCommand(
                            og_values[index],
                            line_edit.text(),
                            self.current_prop_list,
                            index,
                            self.node_tree_model,
                            self.current_node.model_item,
                            self.select_node
                        )
                    )
                    if og_values[index] != line_edit.text() else None
                )
                line_edit.editingFinished.connect(
                    lambda index=prop_index: og_values.update({index: line_edit.text()})
                )
                push_button.clicked.connect(lambda _, line_edit_=line_edit: button_clicked(line_edit_))

            elif type(props[key]) is PropertyFolder:
                def button_clicked(line_edit_):
                    open_dialog = QFileDialog()
                    folder_path = open_dialog.getExistingDirectory(self, "Select folder:", self._package_path)
                    if folder_path:
                        line_edit.setText(relpath(folder_path, self._package_path))
                    line_edit_.editingFinished.emit()

                og_values[prop_index] = props[key].value
                prop_list.append(QWidget(self.dockWidgetContents))
                layout = QHBoxLayout(prop_list[prop_index])
                line_edit = QLineEdit(prop_list[prop_index])
                push_button = QPushButton(prop_list[prop_index])
                push_button.setText("...")
                push_button.setMaximumWidth(30)
                layout.addWidget(line_edit)
                layout.addWidget(push_button)
                layout.setContentsMargins(0, 0, 0, 0)
                line_edit.setText(props[key].value)
                line_edit.textChanged.connect(props[key].set_value)
                line_edit.textChanged.connect(self.current_node.write_attribs)
                line_edit.textChanged.connect(self.current_node.update_item_name)
                line_edit.textChanged.connect(
                    lambda: self.xml_code_changed.emit(self.current_node)
                    if self.settings_dict["General"]["code_refresh"] >= 3 else None
                )
                line_edit.editingFinished.connect(
                    lambda index=prop_index: self.undo_stack.push(
                        self.WidgetLineEditChangeCommand(
                            og_values[index],
                            line_edit.text(),
                            self.current_prop_list,
                            index,
                            self.node_tree_model,
                            self.current_node.model_item,
                            self.select_node
                        )
                    )
                    if og_values[index] != line_edit.text() else None
                )
                line_edit.editingFinished.connect(
                    lambda index=prop_index: og_values.update({index: line_edit.text()})
                )
                push_button.clicked.connect(lambda _, line_edit_=line_edit: button_clicked(line_edit_))

            elif type(props[key]) is PropertyColour:
                def button_clicked(line_edit_):
                    init_colour = QColor("#" + props[key].value)
                    colour_dialog = QColorDialog()
                    colour = colour_dialog.getColor(init_colour, self, "Choose Colour:")
                    if colour.isValid():
                        line_edit.setText(colour.name()[1:])
                    line_edit_.editingFinished.emit()

                def update_button_colour(text):
                    colour = QColor("#" + text)
                    if colour.isValid() and len(text) == 6:
                        push_button.setStyleSheet("background-color: " + colour.name())
                        push_button.setIcon(QIcon())
                    else:
                        push_button.setStyleSheet("background-color: #ffffff")
                        icon = QIcon()
                        icon.addPixmap(QPixmap(join(cur_folder, "resources/logos/logo_danger.png")),
                                       QIcon.Normal, QIcon.Off)
                        push_button.setIcon(icon)

                og_values[prop_index] = props[key].value
                prop_list.append(QWidget(self.dockWidgetContents))
                layout = QHBoxLayout(prop_list[prop_index])
                line_edit = QLineEdit(prop_list[prop_index])
                line_edit.setMaxLength(6)
                push_button = QPushButton(prop_list[prop_index])
                push_button.setMinimumHeight(21)
                push_button.setMinimumWidth(30)
                push_button.setMaximumHeight(21)
                push_button.setMaximumWidth(30)
                layout.addWidget(line_edit)
                layout.addWidget(push_button)
                layout.setContentsMargins(0, 0, 0, 0)
                line_edit.setText(props[key].value)
                update_button_colour(line_edit.text())
                line_edit.textChanged.connect(props[key].set_value)
                line_edit.textChanged.connect(update_button_colour)
                line_edit.textChanged.connect(self.current_node.write_attribs)
                line_edit.textChanged.connect(
                    lambda: self.xml_code_changed.emit(self.current_node)
                    if self.settings_dict["General"]["code_refresh"] >= 3 else None
                )
                line_edit.editingFinished.connect(
                    lambda index=prop_index: self.undo_stack.push(
                        self.WidgetLineEditChangeCommand(
                            og_values[index],
                            line_edit.text(),
                            self.current_prop_list,
                            index,
                            self.node_tree_model,
                            self.current_node.model_item,
                            self.select_node
                        )
                    )
                    if og_values[index] != line_edit.text() else None
                )
                line_edit.editingFinished.connect(
                    lambda index=prop_index: og_values.update({index: line_edit.text()})
                )
                push_button.clicked.connect(lambda _, line_edit_=line_edit: button_clicked(line_edit_))

            self.layout_prop_editor.setWidget(prop_index, QFormLayout.FieldRole, prop_list[prop_index])
            prop_list[prop_index].setObjectName(str(prop_index))
            prop_index += 1

    def run_wizard(self):
        """
        Called when the wizard button is clicked.

        Sets up the main window and runs the wizard.
        """
        def close():
            wizard.deleteLater()
            self.action_Object_Tree.toggled.emit(enabled_tree)
            self.actionObject_Box.toggled.emit(enabled_box)
            self.action_Property_Editor.toggled.emit(enabled_list)
            self.menu_File.setEnabled(True)
            self.menu_Tools.setEnabled(True)
            self.menu_View.setEnabled(True)

        current_index = self.node_tree_model.indexFromItem(self.current_node.model_item)
        enabled_tree = self.action_Object_Tree.isChecked()
        enabled_box = self.actionObject_Box.isChecked()
        enabled_list = self.action_Property_Editor.isChecked()
        self.action_Object_Tree.toggled.emit(False)
        self.actionObject_Box.toggled.emit(False)
        self.action_Property_Editor.toggled.emit(False)
        self.menu_File.setEnabled(False)
        self.menu_Tools.setEnabled(False)
        self.menu_View.setEnabled(False)

        parent_node = self.current_node.getparent()
        original_node = self.current_node

        kwargs = {"package_path": self._package_path}
        wizard = self.current_node.wizard(self, self.current_node, self.xml_code_changed, **kwargs)
        self.splitter.insertWidget(0, wizard)

        wizard.cancelled.connect(close)
        wizard.cancelled.connect(lambda: self.select_node.emit(current_index))
        wizard.finished.connect(close)
        wizard.finished.connect(
            lambda result: self.undo_stack.push(
                self.RunWizardCommand(
                    parent_node,
                    original_node,
                    result,
                    self.node_tree_model,
                    self.select_node
                )
            )
        )
        wizard.finished.connect(lambda: self.select_node.emit(current_index))

    def check_fomod_state(self):
        """
        Checks whether the installer has unsaved changes.
        """
        if not self.undo_stack.isClean():
            msg_box = QMessageBox()
            msg_box.setWindowTitle("The installer has been modified.")
            msg_box.setText("Do you want to save your changes?")
            msg_box.setStandardButtons(QMessageBox.Save |
                                       QMessageBox.Discard |
                                       QMessageBox.Cancel)
            msg_box.setDefaultButton(QMessageBox.Save)
            return msg_box.exec_()
        else:
            return

    def closeEvent(self, event):
        """
        Override the Qt close event to account for unsaved changes.
        :param event:
        """
        answer = self.check_fomod_state()
        if answer == QMessageBox.Save:
            self.save()
        elif answer == QMessageBox.Discard:
            pass
        elif answer == QMessageBox.Cancel:
            event.ignore()


class SettingsDialog(QDialog, window_settings.Ui_Dialog):
    """
    The class for the settings window. Subclassed from QDialog and created in Qt Designer.
    """
    def __init__(self, parent):
        super().__init__(parent=parent)
        self.setupUi(self)

        self.setWindowFlags(Qt.WindowSystemMenuHint | Qt.WindowTitleHint | Qt.Dialog)
        self.label_warning_palette.setPixmap(QPixmap(join(cur_folder, "resources/logos/logo_danger.png")))
        self.label_warning_style.setPixmap(QPixmap(join(cur_folder, "resources/logos/logo_danger.png")))
        self.widget_warning_palette.hide()
        self.widget_warning_style.hide()
        self.settings_dict = read_settings()

        self.buttonBox.accepted.connect(self.accepted)
        self.buttonBox.rejected.connect(self.close)

        self.check_valid_load.stateChanged.connect(self.check_valid_load_ignore.setEnabled)
        self.check_warn_load.stateChanged.connect(self.check_warn_load_ignore.setEnabled)
        self.check_valid_save.stateChanged.connect(self.check_valid_save_ignore.setEnabled)
        self.check_warn_save.stateChanged.connect(self.check_warn_save_ignore.setEnabled)

        self.check_installSteps.stateChanged.connect(self.combo_installSteps.setEnabled)
        self.check_optionalFileGroups.stateChanged.connect(self.combo_optionalFileGroups.setEnabled)
        self.check_type.stateChanged.connect(self.combo_type.setEnabled)
        self.check_defaultType.stateChanged.connect(self.combo_defaultType.setEnabled)

        self.button_colour_required.clicked.connect(
            lambda: self.button_colour_required.setStyleSheet(
                "background-color: " + QColorDialog().getColor(
                    QColor(self.button_colour_required.styleSheet().split()[1]),
                    self,
                    "Choose Colour:"
                ).name()
            )
        )
        self.button_colour_atleastone.clicked.connect(
            lambda: self.button_colour_atleastone.setStyleSheet(
                "background-color: " + QColorDialog().getColor(
                    QColor(self.button_colour_atleastone.styleSheet().split()[1]),
                    self,
                    "Choose Colour:"
                ).name()
            )
        )
        self.button_colour_either.clicked.connect(
            lambda: self.button_colour_either.setStyleSheet(
                "background-color: " + QColorDialog().getColor(
                    QColor(self.button_colour_either.styleSheet().split()[1]),
                    self,
                    "Choose Colour:"
                ).name()
            )
        )
        self.button_colour_reset_required.clicked.connect(
            lambda: self.button_colour_required.setStyleSheet("background-color: #d90027")
        )
        self.button_colour_reset_atleastone.clicked.connect(
            lambda: self.button_colour_atleastone.setStyleSheet("background-color: #d0d02e")
        )
        self.button_colour_reset_either.clicked.connect(
            lambda: self.button_colour_either.setStyleSheet("background-color: #ffaa7f")
        )
        self.combo_style.currentTextChanged.connect(
            lambda text: self.widget_warning_style.show()
            if text != self.settings_dict["Appearance"]["style"]
            else self.widget_warning_style.hide()
        )
        self.combo_palette.currentTextChanged.connect(
            lambda text: self.widget_warning_palette.show()
            if text != self.settings_dict["Appearance"]["palette"]
            else self.widget_warning_palette.hide()
        )

        self.combo_code_refresh.setCurrentIndex(self.settings_dict["General"]["code_refresh"])
        self.check_intro.setChecked(self.settings_dict["General"]["show_intro"])
        self.check_advanced.setChecked(self.settings_dict["General"]["show_advanced"])
        self.check_tutorial.setChecked(self.settings_dict["General"]["tutorial_advanced"])

        self.check_valid_load.setChecked(self.settings_dict["Load"]["validate"])
        self.check_valid_load_ignore.setChecked(self.settings_dict["Load"]["validate_ignore"])
        self.check_warn_load.setChecked(self.settings_dict["Load"]["warnings"])
        self.check_warn_load_ignore.setChecked(self.settings_dict["Load"]["warn_ignore"])

        self.check_valid_save.setChecked(self.settings_dict["Save"]["validate"])
        self.check_valid_save_ignore.setChecked(self.settings_dict["Save"]["validate_ignore"])
        self.check_warn_save.setChecked(self.settings_dict["Save"]["warnings"])
        self.check_warn_save_ignore.setChecked(self.settings_dict["Save"]["warn_ignore"])

        self.check_installSteps.setChecked(self.settings_dict["Defaults"]["installSteps"].enabled())
        self.combo_installSteps.setEnabled(self.settings_dict["Defaults"]["installSteps"].enabled())
        self.combo_installSteps.setCurrentText(self.settings_dict["Defaults"]["installSteps"].value())
        self.check_optionalFileGroups.setChecked(self.settings_dict["Defaults"]["optionalFileGroups"].enabled())
        self.combo_optionalFileGroups.setEnabled(self.settings_dict["Defaults"]["optionalFileGroups"].enabled())
        self.combo_optionalFileGroups.setCurrentText(self.settings_dict["Defaults"]["optionalFileGroups"].value())
        self.check_type.setChecked(self.settings_dict["Defaults"]["type"].enabled())
        self.combo_type.setEnabled(self.settings_dict["Defaults"]["type"].enabled())
        self.combo_type.setCurrentText(self.settings_dict["Defaults"]["type"].value())
        self.check_defaultType.setChecked(self.settings_dict["Defaults"]["defaultType"].enabled())
        self.combo_defaultType.setEnabled(self.settings_dict["Defaults"]["defaultType"].enabled())
        self.combo_defaultType.setCurrentText(self.settings_dict["Defaults"]["defaultType"].value())

        self.button_colour_required.setStyleSheet(
            "background-color: " + self.settings_dict["Appearance"]["required_colour"]
        )
        self.button_colour_atleastone.setStyleSheet(
            "background-color: " + self.settings_dict["Appearance"]["atleastone_colour"]
        )
        self.button_colour_either.setStyleSheet(
            "background-color: " + self.settings_dict["Appearance"]["either_colour"]
        )
        if self.settings_dict["Appearance"]["style"]:
            self.combo_style.setCurrentText(self.settings_dict["Appearance"]["style"])
        else:
            self.combo_style.setCurrentText("Default")
        if self.settings_dict["Appearance"]["palette"]:
            self.combo_palette.setCurrentText(self.settings_dict["Appearance"]["palette"])
        else:
            self.combo_palette.setCurrentText("Default")

    def accepted(self):
        self.settings_dict["General"]["code_refresh"] = self.combo_code_refresh.currentIndex()
        self.settings_dict["General"]["show_intro"] = self.check_intro.isChecked()
        self.settings_dict["General"]["show_advanced"] = self.check_advanced.isChecked()
        self.settings_dict["General"]["tutorial_advanced"] = self.check_tutorial.isChecked()

        self.settings_dict["Load"]["validate"] = self.check_valid_load.isChecked()
        self.settings_dict["Load"]["validate_ignore"] = self.check_valid_load_ignore.isChecked()
        self.settings_dict["Load"]["warnings"] = self.check_warn_load.isChecked()
        self.settings_dict["Load"]["warn_ignore"] = self.check_warn_load_ignore.isChecked()

        self.settings_dict["Save"]["validate"] = self.check_valid_save.isChecked()
        self.settings_dict["Save"]["validate_ignore"] = self.check_valid_save_ignore.isChecked()
        self.settings_dict["Save"]["warnings"] = self.check_warn_save.isChecked()
        self.settings_dict["Save"]["warn_ignore"] = self.check_warn_save_ignore.isChecked()

        self.settings_dict["Defaults"]["installSteps"].set_enabled(self.check_installSteps.isChecked())
        self.settings_dict["Defaults"]["installSteps"].set_value(self.combo_installSteps.currentText())

        self.settings_dict["Defaults"]["optionalFileGroups"].set_enabled(self.check_optionalFileGroups.isChecked())
        self.settings_dict["Defaults"]["optionalFileGroups"].set_value(self.combo_optionalFileGroups.currentText()
                                                                       )
        self.settings_dict["Defaults"]["type"].set_enabled(self.check_type.isChecked())
        self.settings_dict["Defaults"]["type"].set_value(self.combo_type.currentText())

        self.settings_dict["Defaults"]["defaultType"].set_enabled(self.check_defaultType.isChecked())
        self.settings_dict["Defaults"]["defaultType"].set_value(self.combo_defaultType.currentText())

        self.settings_dict["Appearance"]["required_colour"] = self.button_colour_required.styleSheet().split()[1]
        self.settings_dict["Appearance"]["atleastone_colour"] = self.button_colour_atleastone.styleSheet().split()[1]
        self.settings_dict["Appearance"]["either_colour"] = self.button_colour_either.styleSheet().split()[1]
        if self.combo_style.currentText() != "Default":
            self.settings_dict["Appearance"]["style"] = self.combo_style.currentText()
        else:
            self.settings_dict["Appearance"]["style"] = ""
        if self.combo_palette.currentText() != "Default":
            self.settings_dict["Appearance"]["palette"] = self.combo_palette.currentText()
        else:
            self.settings_dict["Appearance"]["palette"] = ""

        makedirs(join(expanduser("~"), ".fomod"), exist_ok=True)
        with open(join(expanduser("~"), ".fomod", ".designer"), "w") as configfile:
            set_encoder_options("json", indent=4)
            configfile.write(encode(self.settings_dict))

        self.close()


class About(QDialog, window_about.Ui_Dialog):
    """
    The class for the about window. Subclassed from QDialog and created in Qt Designer.
    """
    def __init__(self, parent):
        super().__init__(parent=parent)
        self.setupUi(self)

        if parent:
            self.move(
                parent.window().frameGeometry().topLeft() + parent.window().rect().center() - self.rect().center()
            )

        self.setWindowFlags(Qt.WindowTitleHint | Qt.Dialog)

        self.version.setText("Version: " + __version__)

        copyright_text = self.copyright.text()
        new_year = "2016-" + str(datetime.now().year) if datetime.now().year != 2016 else "2016"
        copyright_text = copyright_text.replace("2016", new_year)
        self.copyright.setText(copyright_text)

        self.button.clicked.connect(self.close)


class PreviewMoGui(QWidget, preview_mo.Ui_Form):
    clear_tab_signal = pyqtSignal()
    clear_ui_signal = pyqtSignal()
    invalid_node_signal = pyqtSignal()
    missing_node_signal = pyqtSignal()
    set_labels_signal = pyqtSignal([str, str, str, str])
    create_page_signal = pyqtSignal([object])

    class ScaledLabel(QLabel):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.original_pixmap = None
            self.setMinimumSize(320, 200)

        def set_scalable_pixmap(self, pixmap):
            self.original_pixmap = pixmap
            self.setPixmap(self.original_pixmap.scaled(self.size(), Qt.KeepAspectRatio))

        def resizeEvent(self, event):
            if self.pixmap() and self.original_pixmap:
                self.setPixmap(self.original_pixmap.scaled(event.size(), Qt.KeepAspectRatio))

    class PreviewItem(QStandardItem):
        def set_priority(self, value):
            self.priority = value

    def __init__(self, mo_preview_layout):
        super().__init__()
        self.mo_preview_layout = mo_preview_layout
        self.setupUi(self)
        self.mo_preview_layout.addWidget(self)
        self.label_image = self.ScaledLabel(self)
        self.splitter_label.addWidget(self.label_image)
        self.hide()

        self.button_preview_more.setIcon(QIcon(join(cur_folder, "resources/logos/logo_more.png")))
        self.button_preview_less.setIcon(QIcon(join(cur_folder, "resources/logos/logo_less.png")))
        self.button_preview_more.clicked.connect(self.button_preview_more.hide)
        self.button_preview_more.clicked.connect(self.button_preview_less.show)
        self.button_preview_more.clicked.connect(self.widget_preview.show)
        self.button_preview_less.clicked.connect(self.button_preview_less.hide)
        self.button_preview_less.clicked.connect(self.button_preview_more.show)
        self.button_preview_less.clicked.connect(self.widget_preview.hide)
        self.button_preview_more.clicked.emit()
        self.button_results_more.setIcon(QIcon(join(cur_folder, "resources/logos/logo_more.png")))
        self.button_results_less.setIcon(QIcon(join(cur_folder, "resources/logos/logo_less.png")))
        self.button_results_more.clicked.connect(self.button_results_more.hide)
        self.button_results_more.clicked.connect(self.button_results_less.show)
        self.button_results_more.clicked.connect(self.widget_results.show)
        self.button_results_less.clicked.connect(self.button_results_less.hide)
        self.button_results_less.clicked.connect(self.button_results_more.show)
        self.button_results_less.clicked.connect(self.widget_results.hide)
        self.button_results_less.clicked.emit()

        self.model_files = QStandardItemModel()
        self.tree_results.expanded.connect(
            lambda: self.tree_results.header().resizeSections(QHeaderView.Stretch)
        )
        self.tree_results.collapsed.connect(
            lambda: self.tree_results.header().resizeSections(QHeaderView.Stretch)
        )
        self.tree_results.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree_results.customContextMenuRequested.connect(self.on_custom_context_menu)
        self.model_flags = QStandardItemModel()
        self.list_flags.expanded.connect(
            lambda: self.list_flags.header().resizeSections(QHeaderView.Stretch)
        )
        self.list_flags.collapsed.connect(
            lambda: self.list_flags.header().resizeSections(QHeaderView.Stretch)
        )
        self.reset_models()

        self.label_invalid = QLabel(
            "Select an Installation Step node or one of its children to preview its installer page."
        )
        self.label_invalid.setAlignment(Qt.AlignCenter)
        self.mo_preview_layout.addWidget(self.label_invalid)
        self.label_invalid.hide()

        self.label_missing = QLabel(
            "In order to preview an installer page, create an Installation Step node."
        )
        self.label_missing.setAlignment(Qt.AlignCenter)
        self.mo_preview_layout.addWidget(self.label_missing)
        self.label_missing.hide()

        self.clear_tab_signal.connect(self.clear_tab)
        self.clear_ui_signal.connect(self.clear_ui)
        self.invalid_node_signal.connect(self.invalid_node)
        self.missing_node_signal.connect(self.missing_node)
        self.set_labels_signal.connect(self.set_labels)
        self.create_page_signal.connect(self.create_page)

    def on_custom_context_menu(self, position):
        node_tree_context_menu = QMenu(self.tree_results)

        action_expand = QAction(QIcon(join(cur_folder, "resources/logos/logo_expand.png")), "Expand All", self)
        action_collapse = QAction(QIcon(join(cur_folder, "resources/logos/logo_collapse.png")), "Collapse All", self)

        action_expand.triggered.connect(self.tree_results.expandAll)
        action_collapse.triggered.connect(self.tree_results.collapseAll)

        node_tree_context_menu.addActions([action_expand, action_collapse])

        node_tree_context_menu.move(self.tree_results.mapToGlobal(position))
        node_tree_context_menu.exec_()

    def eventFilter(self, object_, event):
        if event.type() == QEvent.HoverEnter:
            self.label_description.setText(object_.property("description"))
            self.label_image.set_scalable_pixmap(QPixmap(object_.property("image_path")))

        return QWidget().eventFilter(object_, event)

    def clear_ui(self):
        self.label_name.clear()
        self.label_author.clear()
        self.label_version.clear()
        self.label_website.clear()
        self.label_description.clear()
        self.label_image.clear()
        [widget.deleteLater() for widget in [
            self.layout_widget.itemAt(index).widget() for index in range(self.layout_widget.count())
            if self.layout_widget.itemAt(index).widget()
            ]]
        self.reset_models()

    def reset_models(self):
        self.model_files.clear()
        self.model_files.setHorizontalHeaderLabels(["Files Preview", "Source", "Plugin"])
        self.model_files_root = QStandardItem(QIcon(join(cur_folder, "resources/logos/logo_folder.png")), "<root>")
        self.model_files.appendRow(self.model_files_root)
        self.tree_results.setModel(self.model_files)
        self.model_flags.clear()
        self.model_flags.setHorizontalHeaderLabels(["Flag Label", "Flag Value", "Plugin"])
        self.list_flags.setModel(self.model_flags)

    def clear_tab(self):
        for index in reversed(range(self.mo_preview_layout.count())):
            widget = self.mo_preview_layout.itemAt(index).widget()
            if widget is not None:
                widget.hide()

    def invalid_node(self):
        self.clear_tab()
        self.label_invalid.show()

    def missing_node(self):
        self.clear_tab()
        self.label_missing.show()

    def set_labels(self, name, author, version, website):
        self.label_name.setText(name)
        self.label_author.setText(author)
        self.label_version.setText(version)
        self.label_website.setText("<a href = {}>link</a>".format(website))

    # this is pretty horrendous, need to come up with a better way of doing this.
    def create_page(self, page_data):
        group_step = QGroupBox(page_data.name)
        layout_step = QVBoxLayout()
        group_step.setLayout(layout_step)

        check_first_radio = True
        for group in page_data.group_list:
            group_group = QGroupBox(group.name)
            layout_group = QVBoxLayout()
            group_group.setLayout(layout_group)

            for plugin in group.plugin_list:
                if group.type in ["SelectAny", "SelectAll", "SelectAtLeastOne"]:
                    button_plugin = QCheckBox(plugin.name, self)

                    if group.type == "SelectAll":
                        button_plugin.setChecked(True)
                        button_plugin.setEnabled(False)
                    elif group.type == "SelectAtLeastOne":
                        button_plugin.toggled.connect(
                            lambda checked, button=button_plugin: button.setChecked(True)
                            if not checked and not [
                                button for button in [
                                    layout_group.itemAt(index).widget() for index in range(layout_group.count())
                                    if layout_group.itemAt(index).widget()
                                ] if button.isChecked()
                            ]
                            else None
                        )

                elif group.type in ["SelectExactlyOne", "SelectAtMostOne"]:
                    button_plugin = QRadioButton(plugin.name, self)
                    if check_first_radio and not button_plugin.isChecked():
                        button_plugin.animateClick(0)
                        check_first_radio = False

                button_plugin.setProperty("description", plugin.description)
                button_plugin.setProperty("image_path", plugin.image_path)
                button_plugin.setProperty("file_list", plugin.file_list)
                button_plugin.setProperty("folder_list", plugin.folder_list)
                button_plugin.setProperty("flag_list", plugin.flag_list)
                button_plugin.setProperty("type", plugin.type)
                button_plugin.setAttribute(Qt.WA_Hover)

                if plugin.type == "Required":
                    button_plugin.setEnabled(False)
                elif plugin.type == "Recommended":
                    button_plugin.animateClick(0) if not button_plugin.isChecked() else None
                elif plugin.type == "NotUsable":
                    button_plugin.setChecked(False)
                    button_plugin.setEnabled(False)

                button_plugin.toggled.connect(self.reset_models)
                button_plugin.toggled.connect(self.update_installed_files)
                button_plugin.toggled.connect(self.update_set_flags)

                button_plugin.installEventFilter(self)
                button_plugin.setObjectName("preview_button")
                layout_group.addWidget(button_plugin)

            if group.type == "SelectAtMostOne":
                button_none = QRadioButton("None")
                layout_group.addWidget(button_none)

            layout_step.addWidget(group_group)

        self.layout_widget.addWidget(group_step)
        self.reset_models()
        self.update_installed_files()
        self.update_set_flags()
        self.show()

    def update_installed_files(self):
        def recurse_add_items(folder, parent):
            for boop in listdir(folder):  # I was very tired
                if isdir(join(folder, boop)):
                    folder_item = None
                    existing_folder_ = self.model_files.findItems(boop, Qt.MatchRecursive)
                    if existing_folder_:
                        for boopity in existing_folder_:
                            if boopity.parent() is parent:
                                folder_item = boopity
                                break
                    if not folder_item:
                        folder_item = self.PreviewItem(
                            QIcon(join(cur_folder, "resources/logos/logo_folder.png")),
                            boop
                        )
                        folder_item.set_priority(folder_.priority)
                        parent.appendRow([folder_item, QStandardItem(rel_source), QStandardItem(button.text())])
                    recurse_add_items(join(folder, boop), folder_item)

                elif isfile(join(folder, boop)):
                    file_item_ = None
                    existing_file_ = self.model_files.findItems(boop, Qt.MatchRecursive)
                    if existing_file_:
                        for boopity in existing_file_:
                            if boopity.parent() is parent:
                                if folder_.priority < boopity.priority:
                                    file_item_ = boopity
                                    break
                                else:
                                    parent.removeRow(boopity.row())
                                    break
                    if not file_item_:
                        file_item_ = self.PreviewItem(
                            QIcon(join(cur_folder, "resources/logos/logo_file.png")),
                            boop
                        )
                        file_item_.set_priority(folder_.priority)
                        parent.appendRow([file_item_, QStandardItem(rel_source), QStandardItem(button.text())])

        for button in self.findChildren((QCheckBox, QRadioButton), "preview_button"):
            for folder_ in button.property("folder_list"):
                if (button.isChecked() and button.property("type") != "NotUsable" or
                        folder_.always_install or
                        folder_.install_usable and button.property("type") != "NotUsable" or
                        button.property("type") == "Required"):
                    destination = folder_.destination
                    abs_source = folder_.abs_source
                    rel_source = folder_.rel_source
                    parent_item = self.model_files_root

                    destination_split = destination.split("/")
                    if destination_split[0] == ".":
                        destination_split = destination_split[1:]
                    for dest_folder in destination_split:
                        existing_folder_list = self.model_files.findItems(dest_folder, Qt.MatchRecursive)
                        if existing_folder_list:
                            for existing_folder in existing_folder_list:
                                if existing_folder.parent() is parent_item:
                                    parent_item = existing_folder
                                    break
                            continue
                        item_ = self.PreviewItem(
                            QIcon(join(cur_folder, "resources/logos/logo_folder.png")),
                            dest_folder
                        )
                        item_.set_priority(folder_.priority)
                        parent_item.appendRow([item_, QStandardItem(), QStandardItem(button.text())])
                        parent_item = item_

                    if isdir(abs_source):
                        recurse_add_items(abs_source, parent_item)

            for file_ in button.property("file_list"):
                if (button.isChecked() and button.property("type") != "NotUsable" or
                        file_.always_install or
                        file_.install_usable and button.property("type") != "NotUsable" or
                        button.property("type") == "Required"):
                    destination = file_.destination
                    abs_source = file_.abs_source
                    rel_source = file_.rel_source
                    parent_item = self.model_files_root

                    destination_split = destination.split("/")
                    if destination_split[0] == ".":
                        destination_split = destination_split[1:]
                    for dest_folder in destination_split:
                        existing_folder_list = self.model_files.findItems(dest_folder, Qt.MatchRecursive)
                        if existing_folder_list:
                            for existing_folder in existing_folder_list:
                                if existing_folder.parent() is parent_item:
                                    parent_item = existing_folder
                                    break
                            continue
                        item_ = self.PreviewItem(
                            QIcon(join(cur_folder, "resources/logos/logo_folder.png")),
                            dest_folder
                        )
                        item_.set_priority(file_.priority)
                        parent_item.appendRow([item_, QStandardItem(), QStandardItem(button.text())])
                        parent_item = item_

                    source_file = abs_source.split("/")[len(abs_source.split("/")) - 1]
                    file_item = None
                    existing_file_list = self.model_files.findItems(source_file, Qt.MatchRecursive)
                    if existing_file_list:
                        for existing_file in existing_file_list:
                            if existing_file.parent() is parent_item:
                                if file_.priority < existing_file.priority:
                                    file_item = existing_file
                                    break
                                else:
                                    parent_item.removeRow(existing_file.row())
                                    break
                    if not file_item:
                        file_item = self.PreviewItem(
                            QIcon(join(cur_folder, "resources/logos/logo_file.png")),
                            source_file
                        )
                        file_item.set_priority(file_.priority)
                        parent_item.appendRow([file_item, QStandardItem(rel_source), QStandardItem(button.text())])

        self.tree_results.header().resizeSections(QHeaderView.Stretch)

    def update_set_flags(self):
        for button in self.findChildren((QCheckBox, QRadioButton), "preview_button"):
            if button.isChecked():
                for flag in button.property("flag_list"):
                    flag_label = QStandardItem(flag.label)
                    flag_value = QStandardItem(flag.value)
                    flag_plugin = QStandardItem(button.text())
                    existing_flag = self.model_flags.findItems(flag.label)
                    if existing_flag:
                        previous_flag_row = existing_flag[0].row()
                        self.model_flags.removeRow(previous_flag_row)
                        self.model_flags.insertRow(previous_flag_row, [flag_label, flag_value, flag_plugin])
                    else:
                        self.model_flags.appendRow([flag_label, flag_value, flag_plugin])

        self.list_flags.header().resizeSections(QHeaderView.Stretch)


class DefaultsSettings(object):
    def __init__(self, key, default_enabled, default_value):
        self.__enabled = default_enabled
        self.__property_key = key
        self.__property_value = default_value

    def __eq__(self, other):
        if self.enabled() == other.enabled() and self.value() == other.value() and self.key() == other.key():
            return True
        else:
            return False

    def set_enabled(self, enabled):
        self.__enabled = enabled

    def set_value(self, value):
        self.__property_value = value

    def enabled(self):
        return self.__enabled

    def value(self):
        return self.__property_value

    def key(self):
        return self.__property_key


default_settings = {
    "General": {
        "code_refresh": 3,
        "show_intro": True,
        "show_advanced": False,
        "tutorial_advanced": True,
    },
    "Appearance": {
        "required_colour": "#ba4d0e",
        "atleastone_colour": "#d0d02e",
        "either_colour": "#ffaa7f",
        "style": "",
        "palette": "",
    },
    "Defaults": {
        "installSteps": DefaultsSettings("order", True, "Explicit"),
        "optionalFileGroups": DefaultsSettings("order", True, "Explicit"),
        "type": DefaultsSettings("name", True, "Optional"),
        "defaultType": DefaultsSettings("name", True, "Optional"),
    },
    "Load": {
        "validate": True,
        "validate_ignore": False,
        "warnings": True,
        "warn_ignore": True,
    },
    "Save": {
        "validate": True,
        "validate_ignore": False,
        "warnings": True,
        "warn_ignore": True,
    },
    "Recent Files": deque(maxlen=5),
}


def generic_errorbox(title, text, detail=""):
    """
    A function that creates a generic errorbox with the logo_admin.png logo.

    :param title: A string containing the title of the errorbox.
    :param text: A string containing the text of the errorbox.
    :param detail: Optional. A string containing the detail text of the errorbox.
    """
    errorbox = QMessageBox()
    errorbox.setText(text)
    errorbox.setWindowTitle(title)
    errorbox.setDetailedText(detail)
    errorbox.setIconPixmap(QPixmap(join(cur_folder, "resources/logos/logo_admin.png")))
    return errorbox


def read_settings():
    """
    Reads the settings from the ~/.fomod/.designer file. If such a file does not exist it uses the default settings.
    The settings are processed to be ready to be used in Python code.

    :return: The processed settings.
    """
    def deep_merge(a, b, path=None):
        """merges b into a"""
        if path is None:
            path = []
        for key in b:
            if key in a:  # only accept the keys in default settings
                if isinstance(a[key], dict) and isinstance(b[key], dict):
                    deep_merge(a[key], b[key], path + [str(key)])
                elif isinstance(b[key], type(a[key])):
                    a[key] = b[key]
                else:
                    pass  # user has messed with conf files
        return a

    try:
        with open(join(expanduser("~"), ".fomod", ".designer"), "r") as configfile:
            settings_dict = decode(configfile.read())
        deep_merge(default_settings, settings_dict)
        return default_settings
    except (FileNotFoundError, JSONDecodeError):
        return default_settings
