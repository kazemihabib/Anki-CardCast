import os
import re
import subprocess
import tempfile
import shutil
import json
import copy
from aqt import mw
from aqt.qt import *
from aqt.utils import showInfo, getSaveFile, showWarning, askUser, getText
from aqt.gui_hooks import browser_will_show_context_menu

# Default configuration structure
DEFAULT_CONFIG = {
    "current_preset": "Default",
    "last_save_path": "", 
    "presets": {
        "Default": {
            "mode": "notes", 
            "pause_items": 1.0,  
            "pause_fields": 0.5, 
            "note_types": {} 
        }
    }
}

class CardCastConfigDialog(QDialog):
    def __init__(self, parent, selected_card_ids):
        super().__init__(parent)
        self.setWindowTitle("CardCast Configuration")
        self.setMinimumWidth(650)
        self.setMinimumHeight(550)

        # Load config from Anki profile using the new CardCast key
        self.config = mw.col.get_config("cardcast_addon_config", DEFAULT_CONFIG)
        if "presets" not in self.config:
            self.config = DEFAULT_CONFIG
            
        self.selected_card_ids = selected_card_ids
        
        # Determine unique Note Types present in the selection
        self.active_notetypes = {} 
        for cid in selected_card_ids:
            note = mw.col.getCard(cid).note()
            nt_name = note.model()['name']
            if nt_name not in self.active_notetypes:
                self.active_notetypes[nt_name] = [f['name'] for f in note.model()['flds']]
                
        self.current_nt_name = None 
        # Working memory for note types (prevents auto-saving to DB)
        self.current_nt_configs = {}

        self._setup_ui()
        self._load_preset(self.config["current_preset"])

    def _setup_ui(self):
        main_layout = QVBoxLayout()

        # --- 1. Preset Management ---
        preset_group = QGroupBox("Presets")
        preset_layout = QHBoxLayout()
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(self.config["presets"].keys())
        self.preset_combo.currentTextChanged.connect(self._on_preset_changed)
        
        btn_save = QPushButton("Save")
        btn_save.clicked.connect(self._save_preset)
        
        btn_clone = QPushButton("Clone")
        btn_clone.clicked.connect(self._clone_preset)
        
        btn_new = QPushButton("New Preset")
        btn_new.clicked.connect(self._new_preset)
        
        btn_delete = QPushButton("Delete")
        btn_delete.clicked.connect(self._delete_preset)
        
        preset_layout.addWidget(QLabel("Preset:"))
        preset_layout.addWidget(self.preset_combo, 1)
        preset_layout.addWidget(btn_save)
        preset_layout.addWidget(btn_clone)
        preset_layout.addWidget(btn_new)
        preset_layout.addWidget(btn_delete)
        preset_group.setLayout(preset_layout)
        main_layout.addWidget(preset_group)

        # --- 2. Generation Mode ---
        mode_group = QGroupBox("Generation Mode")
        mode_layout = QHBoxLayout()
        self.radio_notes = QRadioButton("Process Unique Notes (Prevents duplicate audio)")
        self.radio_cards = QRadioButton("Process Every Card (Repeats if note has multiple cards)")
        mode_layout.addWidget(self.radio_notes)
        mode_layout.addWidget(self.radio_cards)
        mode_group.setLayout(mode_layout)
        main_layout.addWidget(mode_group)

        # --- 3. Field Ordering per Note Type ---
        ordering_group = QGroupBox("Field Order by Note Type")
        ordering_layout = QHBoxLayout()

        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel("Note Types in Selection:"))
        self.nt_list = QListWidget()
        self.nt_list.addItems(sorted(self.active_notetypes.keys()))
        self.nt_list.currentItemChanged.connect(self._on_nt_selected)
        left_layout.addWidget(self.nt_list)
        
        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel("Fields to Play (Drag to reorder):"))
        self.selected_fields_list = QListWidget()
        self.selected_fields_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.selected_fields_list.model().rowsMoved.connect(self._update_working_memory)
        right_layout.addWidget(self.selected_fields_list)
        
        btn_layout = QHBoxLayout()
        btn_add = QPushButton("↑ Add")
        btn_add.clicked.connect(self._move_to_selected)
        btn_remove = QPushButton("↓ Remove")
        btn_remove.clicked.connect(self._move_to_available)
        btn_layout.addWidget(btn_add)
        btn_layout.addWidget(btn_remove)
        right_layout.addLayout(btn_layout)

        right_layout.addWidget(QLabel("Ignored Fields:"))
        self.available_fields_list = QListWidget()
        right_layout.addWidget(self.available_fields_list)

        ordering_layout.addLayout(left_layout, 1)
        ordering_layout.addLayout(right_layout, 2)
        ordering_group.setLayout(ordering_layout)
        main_layout.addWidget(ordering_group)

        # --- 4. Options (Pauses & Output) ---
        options_group = QGroupBox("Output Options")
        options_layout = QGridLayout()

        options_layout.addWidget(QLabel("Pause between notes/cards (seconds):"), 0, 0)
        self.pause_items_spinbox = QDoubleSpinBox()
        self.pause_items_spinbox.setMinimum(0.0)
        self.pause_items_spinbox.setSingleStep(0.5)
        options_layout.addWidget(self.pause_items_spinbox, 0, 1)

        options_layout.addWidget(QLabel("Pause between fields (seconds):"), 1, 0)
        self.pause_fields_spinbox = QDoubleSpinBox()
        self.pause_fields_spinbox.setMinimum(0.0)
        self.pause_fields_spinbox.setSingleStep(0.5)
        options_layout.addWidget(self.pause_fields_spinbox, 1, 1)

        options_layout.addWidget(QLabel("Save output to:"), 2, 0)
        path_layout = QHBoxLayout()
        self.save_path_edit = QLineEdit()
        self.save_path_edit.setText(self.config.get("last_save_path", ""))
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._browse_save_path)
        path_layout.addWidget(self.save_path_edit)
        path_layout.addWidget(btn_browse)
        options_layout.addLayout(path_layout, 2, 1)

        options_group.setLayout(options_layout)
        main_layout.addWidget(options_group)

        # --- Dialog Buttons ---
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(self.accept_and_run)
        btn_box.rejected.connect(self.reject)
        main_layout.addWidget(btn_box)

        self.setLayout(main_layout)
        
        if self.nt_list.count() > 0:
            self.nt_list.setCurrentRow(0)

    # --- UI Logic ---

    def _browse_save_path(self):
        path = getSaveFile(
            parent=self, 
            title="Choose Output File", 
            dir_description="CardCast Audio", 
            key="cardcast_audio_dir", 
            ext=".mp3"
        )
        if path:
            self.save_path_edit.setText(path)

    def _load_preset(self, preset_name):
        # Prevent triggering events while loading
        self.preset_combo.blockSignals(True)
        
        if preset_name not in self.config["presets"]:
            preset_name = "Default"
            
        self.preset_combo.setCurrentText(preset_name)
        preset_data = self.config["presets"][preset_name]
        
        # Load into working memory (deep copy so we don't accidentally edit the real preset)
        self.current_nt_configs = copy.deepcopy(preset_data.get("note_types", {}))
        
        if preset_data.get("mode") == "cards":
            self.radio_cards.setChecked(True)
        else:
            self.radio_notes.setChecked(True)
            
        p_items = preset_data.get("pause_items", 1.0)
        p_fields = preset_data.get("pause_fields", 0.5)
        
        self.pause_items_spinbox.setValue(p_items)
        self.pause_fields_spinbox.setValue(p_fields)
        
        if self.current_nt_name:
            self._populate_fields(self.current_nt_name)
            
        self.preset_combo.blockSignals(False)

    def _on_preset_changed(self, preset_name):
        if preset_name:
            self._load_preset(preset_name)

    def _update_working_memory(self, *args):
        """Updates the temporary working memory when UI elements change."""
        if not self.current_nt_name:
            return
        selected_fields = [self.selected_fields_list.item(i).text() for i in range(self.selected_fields_list.count())]
        self.current_nt_configs[self.current_nt_name] = selected_fields

    def _on_nt_selected(self, current, previous):
        # Ensure changes to the previous note type are saved to working memory before switching
        if previous:
            self._update_working_memory()
            
        if not current:
            return
        self.current_nt_name = current.text()
        self._populate_fields(self.current_nt_name)

    def _populate_fields(self, nt_name):
        self.selected_fields_list.clear()
        self.available_fields_list.clear()
        
        all_fields = self.active_notetypes.get(nt_name, [])
        # Load from working memory, NOT directly from the preset
        saved_selected = self.current_nt_configs.get(nt_name, [])
        
        valid_selected = [f for f in saved_selected if f in all_fields]
        for field in valid_selected:
            self.selected_fields_list.addItem(field)
        for field in all_fields:
            if field not in valid_selected:
                self.available_fields_list.addItem(field)

    def _move_to_selected(self):
        for item in self.available_fields_list.selectedItems():
            self.selected_fields_list.addItem(self.available_fields_list.takeItem(self.available_fields_list.row(item)))
        self._update_working_memory()

    def _move_to_available(self):
        for item in self.selected_fields_list.selectedItems():
            self.available_fields_list.addItem(self.selected_fields_list.takeItem(self.selected_fields_list.row(item)))
        self._update_working_memory()

    def _save_preset(self):
        """Explicitly saves current UI state to the active preset."""
        preset_name = self.preset_combo.currentText()
        self._update_working_memory() # ensure latest field changes are caught
        
        self.config["presets"][preset_name]["mode"] = "notes" if self.radio_notes.isChecked() else "cards"
        self.config["presets"][preset_name]["pause_items"] = self.pause_items_spinbox.value()
        self.config["presets"][preset_name]["pause_fields"] = self.pause_fields_spinbox.value()
        self.config["presets"][preset_name]["note_types"] = copy.deepcopy(self.current_nt_configs)
        
        mw.col.set_config("cardcast_addon_config", self.config)
        showInfo(f"Preset '{preset_name}' saved successfully.", parent=self)

    def _clone_preset(self):
        """Creates a new preset using the CURRENT SCREEN STATE."""
        name, ok = getText("Enter name for cloned preset:", parent=self)
        if ok and name:
            if name in self.config["presets"]:
                showWarning("Preset already exists!", parent=self)
                return
            
            self._update_working_memory()
            self.config["presets"][name] = {
                "mode": "notes" if self.radio_notes.isChecked() else "cards",
                "pause_items": self.pause_items_spinbox.value(),
                "pause_fields": self.pause_fields_spinbox.value(),
                "note_types": copy.deepcopy(self.current_nt_configs)
            }
            
            self.preset_combo.addItem(name)
            self.preset_combo.setCurrentText(name)
            self._save_preset() # Automatically save the new clone to DB

    def _new_preset(self):
        """Creates a completely blank/default preset."""
        name, ok = getText("Enter name for new preset:", parent=self)
        if ok and name:
            if name in self.config["presets"]:
                showWarning("Preset already exists!", parent=self)
                return
            
            self.config["presets"][name] = {
                "mode": "notes",
                "pause_items": 1.0,
                "pause_fields": 0.5,
                "note_types": {}
            }
            
            self.preset_combo.addItem(name)
            self.preset_combo.setCurrentText(name)
            self._save_preset()

    def _delete_preset(self):
        name = self.preset_combo.currentText()
        if name == "Default":
            showWarning("Cannot delete the Default preset.", parent=self)
            return
        if askUser(f"Are you sure you want to delete preset '{name}'?", parent=self):
            del self.config["presets"][name]
            self.preset_combo.removeItem(self.preset_combo.findText(name))

    def accept_and_run(self):
        """Runs the generation using current UI state WITHOUT overwriting preset config."""
        save_path = self.save_path_edit.text().strip()
        if not save_path:
            showWarning("Please specify a save location before generating.", parent=self)
            return

        # We only save the global properties (so it remembers your last save folder)
        self.config["current_preset"] = self.preset_combo.currentText()
        self.config["last_save_path"] = save_path
        mw.col.set_config("cardcast_addon_config", self.config)
        
        self._update_working_memory()
        self.accept()

    def get_run_configuration(self):
        """Generates configuration strictly from the UI / working memory."""
        return {
            "output_path": self.save_path_edit.text().strip(),
            "mode": "notes" if self.radio_notes.isChecked() else "cards",
            "pause_items": self.pause_items_spinbox.value(),
            "pause_fields": self.pause_fields_spinbox.value(),
            "note_types": copy.deepcopy(self.current_nt_configs)
        }


# --- Core Logic ---

def extract_audio_tags(field_content):
    pattern = r'\[sound:(.*?)\]'
    return re.findall(pattern, field_content)

def get_ffmpeg_path():
    if cmd := shutil.which("ffmpeg"):
        return cmd
    for custom_path in ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]:
        if os.path.exists(custom_path):
            return custom_path
    return "ffmpeg"

def generate_audio(browser):
    selected_card_ids = browser.selectedCards()
    if not selected_card_ids:
        showInfo("Please select at least one card.")
        return

    dialog = CardCastConfigDialog(browser, selected_card_ids)
    if not dialog.exec():
        return 

    config = dialog.get_run_configuration()
    output_path = config["output_path"]

    items_to_process = []
    if config.get("mode") == "notes":
        seen_nids = set()
        for cid in selected_card_ids:
            nid = mw.col.getCard(cid).nid
            if nid not in seen_nids:
                items_to_process.append(nid)
                seen_nids.add(nid)
    else:
        items_to_process = [mw.col.getCard(cid).nid for cid in selected_card_ids]

    items_audio = [] 
    media_dir = mw.col.media.dir()
    nt_configs = config.get("note_types", {})

    for nid in items_to_process:
        note = mw.col.getNote(nid)
        nt_name = note.model()['name']
        fields_to_process = nt_configs.get(nt_name, [])
        
        current_item_audio = []
        for field in fields_to_process:
            if field in note:
                files = extract_audio_tags(note[field])
                for f in files:
                    full_path = os.path.join(media_dir, f)
                    if os.path.exists(full_path):
                        current_item_audio.append(full_path)
        
        if current_item_audio:
            items_audio.append(current_item_audio)

    if not items_audio:
        showWarning("No valid audio files found in the configured fields for the selected items.")
        return

    process_with_ffmpeg(items_audio, config.get("pause_items", 1.0), config.get("pause_fields", 0.5), output_path)


def process_with_ffmpeg(items_audio, pause_items, pause_fields, output_path):
    ffmpeg_cmd = get_ffmpeg_path()
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            silence_item_path = os.path.join(temp_dir, "silence_item.mp3")
            silence_field_path = os.path.join(temp_dir, "silence_field.mp3")

            def generate_silence(duration, path):
                if duration > 0:
                    subprocess.run([
                        ffmpeg_cmd, "-y", "-f", "lavfi", 
                        "-i", "anullsrc=r=44100:cl=stereo", 
                        "-t", str(duration),
                        "-q:a", "9", path
                    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            generate_silence(pause_items, silence_item_path)
            generate_silence(pause_fields, silence_field_path)

            list_path = os.path.join(temp_dir, "concat_list.txt")
            with open(list_path, "w", encoding="utf-8") as f:
                for i, item in enumerate(items_audio):
                    for j, audio_file in enumerate(item):
                        f.write(f"file '{audio_file}'\n")
                        if j < len(item) - 1 and pause_fields > 0:
                            f.write(f"file '{silence_field_path}'\n")
                    
                    if i < len(items_audio) - 1 and pause_items > 0:
                        f.write(f"file '{silence_item_path}'\n")

            subprocess.run([
                ffmpeg_cmd, "-y", "-f", "concat", "-safe", "0",
                "-i", list_path,
                "-c:a", "libmp3lame", 
                "-q:a", "2", 
                output_path
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        showInfo(f"Successfully generated CardCast audio!\nSaved to: {output_path}")

    except Exception as e:
        showWarning(f"An error occurred while processing audio.\nCommand used: {ffmpeg_cmd}\nError details: {str(e)}")

def add_context_menu_action(browser, menu):
    action = QAction("Generate CardCast Audio...", browser)
    action.triggered.connect(lambda: generate_audio(browser))
    menu.addAction(action)

browser_will_show_context_menu.append(add_context_menu_action)