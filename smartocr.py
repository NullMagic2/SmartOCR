import os
import sys
import io
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from PIL import Image, ImageTk, Image as PILImage
from pdf2image import convert_from_path, pdfinfo_from_path
import lmstudio as lms  # LM Studio Python SDK
import base64
import tempfile
import queue # Import queue for thread communication if needed later, but using 'after' for now
import traceback # For printing full tracebacks

# Global default model name
default_model='gemma-3-12b-it-qat'

# --- Helper Functions ---

def center_window(win, width, height):
    """Centers a Tkinter window on the screen."""
    win.update_idletasks()
    screen_width = win.winfo_screenwidth()
    screen_height = win.winfo_screenheight()
    x = (screen_width - width) // 2
    y = (screen_height - height) // 2
    win.geometry(f"{width}x{height}+{x}+{y}")

def prepare_image_for_lmstudio_base64_tempfile(pil_image):
    """
    Saves a PIL image to a temporary file and prepares it for LM Studio.
    Returns the image handle and the temporary file path.
    Remember to delete the temporary file later if needed.
    """
    buffered = io.BytesIO()
    pil_image.save(buffered, format="PNG") # Ensure PNG format for consistency
    img_bytes = buffered.getvalue()
    tmpfile_path = None # Initialize
    try:
        # Create a temporary file that persists until explicitly deleted
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmpfile:
            tmpfile.write(img_bytes)
            tmpfile_path = tmpfile.name
        # Prepare the image using LM Studio SDK
        image_handle = lms.prepare_image(tmpfile_path)
        return image_handle, tmpfile_path
    except Exception as e:
        print(f"[ERROR] Failed during image preparation/tempfile creation: {e}")
        # Clean up temp file if it was created before the error
        if tmpfile_path and os.path.exists(tmpfile_path):
            try:
                os.unlink(tmpfile_path)
            except OSError as unlink_e:
                print(f"[ERROR] Failed to delete temporary file {tmpfile_path}: {unlink_e}")
        raise # Re-raise the exception

def debug_describe_image(pil_image, model_name=default_model):
    """Optional: Sends an image to LM Studio for a basic description."""
    tmpfile_path = None
    try:
        image_handle, tmpfile_path = prepare_image_for_lmstudio_base64_tempfile(pil_image)
        print(f"[DEBUG][Describe] Image handle: {image_handle} (temp file: {tmpfile_path})")
        model = lms.llm(model_name)
        chat = lms.Chat()
        chat.add_user_message(
            "Describe what you see in this image in 2-3 lines at most. Do not say anything else.",
            images=[image_handle]
        )
        prediction = model.respond(chat)
        print(f"[DEBUG][Describe] Model output: {prediction}")
    except Exception as e:
        print(f"[ERROR][Describe] Model error: {e}")
    finally:
        # Ensure temporary file is deleted
        if tmpfile_path and os.path.exists(tmpfile_path):
            try:
                os.unlink(tmpfile_path)
                # print(f"[DEBUG][Describe] Deleted temp file: {tmpfile_path}")
            except OSError as unlink_e:
                print(f"[ERROR][Describe] Failed to delete temp file {tmpfile_path}: {unlink_e}")


def ocr_page(pil_image, model_name=default_model):
    """
    Performs OCR on a single PIL image using LM Studio.
    Handles temporary file creation and deletion.
    Returns the prediction result object from LM Studio or an error string.
    """
    image_handle = None
    tmpfile_path = None
    try:
        image_handle, tmpfile_path = prepare_image_for_lmstudio_base64_tempfile(pil_image)
        model = lms.llm(model_name)
        chat = lms.Chat()
        chat.add_user_message(
            "Transcribe the contents of this image into plain text, and try to keep as close as possible to the original layout. Do not say anything else.",
            images=[image_handle]
        )
        prediction = model.respond(chat)
        return prediction # Return the result object
    except Exception as e:
        print(f"  [ERROR][OCR] Model error during OCR for temp file {tmpfile_path}: {e}")
        return f"Error: {e}" # Simplification: return error string
    finally:
        # Ensure temporary file is deleted after LM Studio processing
        if tmpfile_path and os.path.exists(tmpfile_path):
            try:
                os.unlink(tmpfile_path)
            except OSError as unlink_e:
                print(f"  [ERROR][OCR] Failed to delete temp file {tmpfile_path}: {unlink_e}")


# --- Main Application Class ---

class PDFPreviewer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PDF Previewer & OCR Converter - Threaded")
        center_window(self, 1000, 800)
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        # --- Instance Variables ---
        self.pdf_file = None
        self.total_pages = 0
        self.current_page_index = 0
        self.original_pil = None # Holds the PIL image for the *current* preview
        self.photo = None # Holds the PhotoImage for the canvas
        self.ocr_thread = None # To keep track of the running OCR thread
        self.cancel_event = None # Event flag for cancellation

        # --- GUI Setup ---
        # Main window grid setup
        self.rowconfigure(0, weight=0)
        self.rowconfigure(1, weight=1)
        self.rowconfigure(2, weight=0)
        self.columnconfigure(0, weight=3, minsize=600)
        self.columnconfigure(1, weight=2, minsize=400)

        # --- TOP ROW: Navigation ---
        self.nav_frame_left = tk.Frame(self)
        self.nav_frame_left.grid(row=0, column=0, sticky="w", padx=10, pady=5)

        self.load_button = tk.Button(self.nav_frame_left, text="Load PDF", command=self.load_pdf_file)
        self.load_button.grid(row=0, column=0, padx=(0,10))
        self.prev_button = tk.Button(self.nav_frame_left, text="←", command=self.prev_page, state=tk.DISABLED)
        self.prev_button.grid(row=0, column=1, padx=5)
        self.next_button = tk.Button(self.nav_frame_left, text="→", command=self.next_page, state=tk.DISABLED)
        self.next_button.grid(row=0, column=2, padx=5)
        tk.Label(self.nav_frame_left, text="Go to page:").grid(row=0, column=3, padx=(15,5))
        # Go To Entry remains enabled (once a PDF is loaded)
        self.go_to_page_entry = tk.Entry(self.nav_frame_left, width=5, state=tk.DISABLED)
        self.go_to_page_entry.grid(row=0, column=4, padx=5)
        # Go Button remains enabled (once a PDF is loaded)
        self.go_button = tk.Button(self.nav_frame_left, text="Go", command=self.goto_page, state=tk.DISABLED)
        self.go_button.grid(row=0, column=5, padx=5)

        self.nav_frame_right = tk.Frame(self)
        self.nav_frame_right.grid(row=0, column=1, sticky="w", padx=10, pady=5)
        self.results_label = tk.Label(self.nav_frame_right, text="Conversion Results:", anchor="w")
        self.results_label.pack(side=tk.LEFT)

        # --- MAIN CONTENT: Preview Canvas and Results Text ---
        self.preview_frame = tk.Frame(self)
        self.preview_frame.grid(row=1, column=0, sticky="nsew", padx=(10,5), pady=5)
        self.preview_frame.rowconfigure(0, weight=1)
        self.preview_frame.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(self.preview_frame, bg="grey")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas_image_id = self.canvas.create_image(0, 0, anchor=tk.NW)
        self.canvas.bind("<Configure>", self.on_canvas_resize)

        self.results_frame = tk.Frame(self)
        self.results_frame.grid(row=1, column=1, sticky="nsew", padx=(5,10), pady=5)
        self.results_frame.rowconfigure(0, weight=1)
        self.results_frame.columnconfigure(0, weight=1)
        self.results_text = scrolledtext.ScrolledText(self.results_frame, wrap=tk.WORD)
        self.results_text.grid(row=0, column=0, sticky="nsew")

        # --- BOTTOM ROW: Status, Controls ---
        self.bottom_left_frame = tk.Frame(self)
        self.bottom_left_frame.grid(row=2, column=0, sticky="w", padx=(10,5), pady=(5,10))
        self.status_label = tk.Label(self.bottom_left_frame, text="No document loaded.")
        self.status_label.grid(row=0, column=0, sticky="w", pady=5)
        self.range_frame = tk.Frame(self.bottom_left_frame)
        self.range_frame.grid(row=1, column=0, sticky="w")
        tk.Label(self.range_frame, text="Convert from page:").grid(row=0, column=0, padx=5)
        self.from_entry = tk.Entry(self.range_frame, width=5, state=tk.DISABLED)
        self.from_entry.grid(row=0, column=1, padx=5)
        tk.Label(self.range_frame, text="to:").grid(row=0, column=2, padx=5)
        self.to_entry = tk.Entry(self.range_frame, width=5, state=tk.DISABLED)
        self.to_entry.grid(row=0, column=3, padx=5)
        tk.Label(self.range_frame, text="(Leave empty for all)").grid(row=0, column=4, padx=5)

        # OCR Control Buttons Frame
        self.ocr_control_frame = tk.Frame(self.bottom_left_frame)
        self.ocr_control_frame.grid(row=2, column=0, pady=10, sticky="w")
        self.convert_button = tk.Button(self.ocr_control_frame, text="Start Conversion", command=self.run_ocr, state=tk.DISABLED)
        self.convert_button.pack(side=tk.LEFT, padx=(0, 10))
        self.save_button = tk.Button(self.ocr_control_frame, text="Save OCR", command=self._prompt_save, state=tk.NORMAL) # Enabled by default
        self.save_button.pack(side=tk.LEFT, padx=(0, 10))
        self.cancel_button = tk.Button(self.ocr_control_frame, text="Cancel OCR", command=self.cancel_ocr, state=tk.DISABLED)
        self.cancel_button.pack(side=tk.LEFT)

        self.bottom_right_frame = tk.Frame(self)
        self.bottom_right_frame.grid(row=2, column=1, sticky="e", padx=(5,10), pady=(5,10))


    def on_canvas_resize(self, event):
        """Handles canvas resize events to update the preview image."""
        if self.original_pil:
            self.update_preview_image()

    def update_preview_image(self):
        """Resizes and updates the image displayed on the canvas."""
        if not self.original_pil:
            if self.canvas.winfo_exists():
                self.canvas.delete(self.canvas_image_id)
                self.canvas_image_id = self.canvas.create_image(0, 0, anchor=tk.NW)
            self.photo = None
            return

        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 1 or ch < 1 or not self.canvas.winfo_exists():
            return

        try:
            resized_img = self.original_pil.copy()
            resized_img.thumbnail((cw, ch), PILImage.Resampling.LANCZOS)
            self.photo = ImageTk.PhotoImage(resized_img)
            self.canvas.itemconfig(self.canvas_image_id, image=self.photo)
            self.canvas.image = self.photo
        except Exception as e:
            print(f"[ERROR] Failed to update preview image: {e}")


    def load_pdf_file(self):
        """Handles the 'Load PDF' button action."""
        if self.ocr_thread and self.ocr_thread.is_alive():
            self._show_messagebox('warning', "Busy", "An OCR conversion is currently in progress. Please wait.")
            return

        pdf_file_path = filedialog.askopenfilename(title="Select PDF File", filetypes=[("PDF Files", "*.pdf")])
        if pdf_file_path:
            print("Loading new PDF...")
            self.pdf_file = pdf_file_path
            self.results_text.delete("1.0", tk.END)
            self.total_pages = 0
            self.current_page_index = 0
            self.original_pil = None
            self.photo = None
            self.update_preview_image()
            self.status_label.config(text="Loading Document Info...")
            self.update_idletasks()
            # Disable all controls except GoTo/Save during initial load
            self._set_ocr_initiation_controls_state(tk.DISABLED)
            self._set_navigation_state(tk.DISABLED)
            self._set_cancel_button_state(tk.DISABLED)
            self._set_save_button_state(tk.NORMAL) # Keep save enabled
            self._set_button_state(self.go_to_page_entry, tk.DISABLED) # Keep GoTo disabled until loaded
            self._set_button_state(self.go_button, tk.DISABLED)

            info_thread = threading.Thread(target=self._load_document_info_worker, daemon=True)
            info_thread.start()
        else:
            if not self.pdf_file:
                self._show_messagebox('info', "No PDF Selected", "No PDF file was selected.")
                self.status_label.config(text="No document loaded.")


    def _load_document_info_worker(self):
        """Worker thread function to get PDF info."""
        try:
            info = pdfinfo_from_path(self.pdf_file, timeout=15)
            page_count = info.get("Pages", 0)
            self.after(0, self._update_page_count, page_count)
        except Exception as e:
            self.after(0, self._show_messagebox, 'error', "Info Error", f"Could not retrieve PDF info: {e}")
            self.after(0, self._update_status, "Error loading document info.")
            self.after(0, self._set_button_state, self.load_button, tk.NORMAL) # Only re-enable load
            return

        if page_count > 0:
            try:
                images = convert_from_path(self.pdf_file, first_page=1, last_page=1, timeout=15)
                if images:
                    self.after(0, self.on_page_loaded, 0, images[0])
                else:
                    self.after(0, self._show_messagebox, 'error', "Load Error", "Could not load the first page image.")
                    self.after(0, self._update_status, "Error loading first page.")
                    self.after(0, self._set_button_state, self.load_button, tk.NORMAL)
            except Exception as e:
                self.after(0, self._show_messagebox, 'error', "Load Error", f"Error loading first page image: {e}")
                self.after(0, self._update_status, "Error loading first page.")
                self.after(0, self._set_button_state, self.load_button, tk.NORMAL)
        else:
            self.after(0, self._update_status, "PDF loaded, but reports 0 pages.")
            self.after(0, self._set_button_state, self.load_button, tk.NORMAL)


    def _update_page_count(self, count):
        """Safely updates the total_pages count from the worker thread."""
        self.total_pages = count
        print(f"[DEBUG] Total pages in PDF: {self.total_pages}")


    def on_page_loaded(self, page_index, pil_image):
        """Callback executed in main thread when a page image is ready for preview."""
        self.current_page_index = page_index
        self.original_pil = pil_image
        self.update_preview_image() # This updates the canvas
        self.status_label.config(text=f"Previewing page {page_index + 1} of {self.total_pages}")

        # Enable relevant controls now that a page is loaded
        # Check OCR state specifically for the convert button
        if not (self.ocr_thread and self.ocr_thread.is_alive()):
             self._set_ocr_initiation_controls_state(tk.NORMAL)
             self._set_cancel_button_state(tk.DISABLED)
        else: # OCR is running
             self._set_ocr_initiation_controls_state(tk.DISABLED)
             self._set_cancel_button_state(tk.NORMAL)

        self._set_navigation_state(tk.NORMAL)
        self._set_save_button_state(tk.NORMAL)
        self._set_button_state(self.go_to_page_entry, tk.NORMAL)
        self._set_button_state(self.go_button, tk.NORMAL)

        # Set Prev/Next state based on page index
        self._set_button_state(self.prev_button, tk.NORMAL if page_index > 0 else tk.DISABLED)
        self._set_button_state(self.next_button, tk.NORMAL if page_index < self.total_pages - 1 else tk.DISABLED)

        # Clear page range entries for convenience
        if self.from_entry.winfo_exists(): self.from_entry.delete(0, tk.END)
        if self.to_entry.winfo_exists(): self.to_entry.delete(0, tk.END)


    def show_page(self, index):
        """Initiates loading and display of a specific page index."""
        # Page change IS allowed during OCR, but loading happens in background thread
        if not self.pdf_file or not (0 <= index < self.total_pages):
            print(f"[WARN] Attempted to show invalid page index {index} or no PDF loaded.")
            return

        # Disable navigation *during page load* to prevent rapid clicks / race conditions
        self._set_navigation_state(tk.DISABLED)
        self._update_status(f"Loading page {index + 1} preview...")

        load_thread = threading.Thread(target=self._load_specific_page_worker, args=(index,), daemon=True)
        load_thread.start()


    def _load_specific_page_worker(self, index):
        """Worker thread to load a specific page image for preview."""
        ocr_running = self.ocr_thread and self.ocr_thread.is_alive()
        # Try to get current status safely, fallback if needed
        try:
             original_status = self.status_label.cget("text") if self.status_label.winfo_exists() else "Loading..."
        except tk.TclError:
             original_status = "Loading..."

        try:
            images = convert_from_path(self.pdf_file, first_page=index + 1, last_page=index + 1, timeout=15)
            if images:
                # Schedule update on main thread - on_page_loaded handles re-enabling nav
                self.after(0, self.on_page_loaded, index, images[0])
            else:
                # If load fails, revert status and re-enable navigation
                self.after(0, self._show_messagebox, 'error', "Load Error", f"Could not load page {index + 1}.")
                self.after(0, self._update_status, original_status)
                self.after(0, self._set_navigation_state, tk.NORMAL)
        except Exception as e:
            self.after(0, self._show_messagebox, 'error', "Load Error", f"Failed to load page {index + 1}: {e}")
            self.after(0, self._update_status, original_status)
            self.after(0, self._set_navigation_state, tk.NORMAL)


    def next_page(self):
        """Handles the 'Next Page' button action."""
        if self.current_page_index < self.total_pages - 1:
            self.show_page(self.current_page_index + 1)

    def prev_page(self):
        """Handles the 'Previous Page' button action."""
        if self.current_page_index > 0:
            self.show_page(self.current_page_index - 1)

    def goto_page(self):
        """Handles the 'Go' button action for page navigation."""
        # No check for OCR running here; show_page handles the interaction gracefully
        try:
            page_num_str = self.go_to_page_entry.get().strip()
            if not page_num_str: return
            page_num = int(page_num_str) - 1
            # Call show_page - it will start the background load
            self.show_page(page_num)
            # Check validity *after* calling show_page to avoid duplicate messages if show_page handles it
            if not (0 <= page_num < self.total_pages):
                 self._show_messagebox('error', "Error", f"Page number must be between 1 and {self.total_pages}.")

        except ValueError:
            self._show_messagebox('error', "Error", "Please enter a valid page number.")
        finally:
            # Clear the entry regardless of outcome
            if self.go_to_page_entry.winfo_exists():
                self.go_to_page_entry.delete(0, tk.END)


    # --- Thread-Safe GUI Update Helpers ---

    def _update_status(self, message):
        """Safely updates the status label from any thread."""
        if hasattr(self, 'status_label') and self.status_label.winfo_exists():
            self.status_label.config(text=message)

    def _append_text_to_results(self, prefix, text):
        """Safely appends text to the results ScrolledText widget."""
        if hasattr(self, 'results_text') and self.results_text.winfo_exists():
            # Ensure widget is not disabled (though it shouldn't be)
            if str(self.results_text.cget('state')) == tk.NORMAL:
                self.results_text.insert(tk.END, prefix + text)
                self.results_text.see(tk.END)
            else:
                print("[WARN] Attempted to append text to disabled results widget.")


    def _show_messagebox(self, msg_type, title, message):
        """Safely shows a messagebox from the main thread."""
        if self.winfo_exists():
            if msg_type == 'info':    messagebox.showinfo(title, message, parent=self)
            elif msg_type == 'warning': messagebox.showwarning(title, message, parent=self)
            elif msg_type == 'error':   messagebox.showerror(title, message, parent=self)

    def _set_button_state(self, widget, state):
         """Safely sets the state of a widget if it exists."""
         if widget and widget.winfo_exists():
             try:
                 widget.config(state=state)
             except tk.TclError as e:
                 print(f"[WARN] TclError setting state for {widget}: {e}")


    def _set_ocr_initiation_controls_state(self, state):
         """Enable/disable controls directly related to starting OCR."""
         widgets_to_toggle = [
             self.convert_button, self.from_entry, self.to_entry
         ]
         for widget in widgets_to_toggle:
              self._set_button_state(widget, state)

    def _set_navigation_state(self, state):
         """Enable/disable only page-turning navigation controls."""
         widgets_to_toggle = [
             self.prev_button, self.next_button, self.load_button
         ]
         for widget in widgets_to_toggle:
              self._set_button_state(widget, state)

    def _set_cancel_button_state(self, state):
        """Safely sets the state of the Cancel button."""
        self._set_button_state(self.cancel_button, state)

    def _set_save_button_state(self, state):
        """Safely sets the state of the Save button."""
        self._set_button_state(self.save_button, state)


    # --- OCR Control ---

    def run_ocr(self, batch_size=10):
        """
        Starts the OCR process in a background thread.
        """
        if self.ocr_thread and self.ocr_thread.is_alive(): self._show_messagebox('warning', "Busy", "An OCR conversion is already in progress."); return
        if not self.pdf_file: self._show_messagebox('error', "Error", "No PDF file loaded."); return
        if self.total_pages <= 0: self._show_messagebox('error', "Error", "Cannot determine total pages or PDF has 0 pages. Please reload."); return
        if not isinstance(batch_size, int) or batch_size < 1: print(f"[WARN] Invalid batch_size '{batch_size}'. Using 10."); batch_size = 10

        from_str = self.from_entry.get().strip(); to_str = self.to_entry.get().strip()
        start_page_num = 1; end_page_num = self.total_pages
        try:
            if from_str or to_str:
                if not from_str or not to_str: self._show_messagebox('error', "Input Error", "Both 'from' and 'to' must be filled or both empty."); return
                start_page_num = int(from_str); end_page_num = int(to_str)
                if not (1 <= start_page_num <= self.total_pages): self._show_messagebox('error', "Input Error", f"'From' page must be 1-{self.total_pages}."); return
                if not (1 <= end_page_num <= self.total_pages): self._show_messagebox('error', "Input Error", f"'To' page must be 1-{self.total_pages}."); return
                if start_page_num > end_page_num: self._show_messagebox('error', "Input Error", "'From' page > 'to' page."); return
        except ValueError: self._show_messagebox('error', "Input Error", "Page numbers must be integers."); return

        # --- Update button states ---
        self._set_ocr_initiation_controls_state(tk.DISABLED) # Disable Start, From/To
        self._set_navigation_state(tk.DISABLED) # Disable Prev/Next/Load
        self._set_cancel_button_state(tk.NORMAL) # Enable Cancel
        self._set_save_button_state(tk.NORMAL)   # Keep Save enabled
        # GoTo Entry/Button remain enabled

        self._update_status(f"Starting OCR for pages {start_page_num} to {end_page_num}...")
        self.cancel_event = threading.Event()

        self.ocr_thread = threading.Thread(
            target=self._ocr_worker_thread,
            args=(self.pdf_file, start_page_num, end_page_num, batch_size, self.total_pages, self.cancel_event),
            daemon=True
        )
        self.ocr_thread.start()


    def cancel_ocr(self):
        """Signals the OCR worker thread to stop."""
        if self.ocr_thread and self.ocr_thread.is_alive():
            if self.cancel_event:
                print("[INFO] Requesting OCR cancellation...")
                self._update_status("Cancellation requested...")
                self.cancel_event.set()
                self._set_cancel_button_state(tk.DISABLED)
            else: print("[WARN] Cancel OCR called, but cancel_event is not set.")
        else: print("[INFO] Cancel OCR called, but no OCR thread is active.")


    def _ocr_worker_thread(self, pdf_filepath, start_page, end_page, batch_size, total_doc_pages, cancel_event):
        """
        Performs the actual PDF loading and OCR processing in the background.
        Checks the cancel_event periodically. Does NOT modify text box on cancel.
        Uses self.after() to schedule GUI updates on the main thread.
        """
        total_pages_to_process_in_run = end_page - start_page + 1
        processed_page_count_in_run = 0
        any_batch_processed_successfully = False
        was_cancelled = False
        final_status = "OCR process initiated."

        try:
            # --- Batch Processing Loop ---
            for batch_start_page in range(start_page, end_page + 1, batch_size):
                if cancel_event.is_set(): print("[INFO] [Thread] Cancel detected before batch."); was_cancelled = True; final_status = "OCR process cancelled by user."; break

                batch_end_page = min(batch_start_page + batch_size - 1, end_page)
                self.after(0, self._update_status, f"Loading batch: Pages {batch_start_page} to {batch_end_page}...")
                print(f"[INFO] [Thread] Loading batch: {batch_start_page}-{batch_end_page}")

                # --- Load Batch ---
                try:
                    current_batch_images = convert_from_path(
                        pdf_filepath, first_page=batch_start_page, last_page=batch_end_page, timeout=30
                    )
                    if not current_batch_images: raise ValueError("pdf2image returned no images.")
                except Exception as e:
                    if cancel_event.is_set(): print("[INFO] [Thread] Cancel detected during load error."); was_cancelled = True; final_status = "OCR cancelled."; break
                    error_message = f"Could not load batch {batch_start_page}-{batch_end_page}: {e}"
                    print(f"[ERROR] [Thread] {error_message}")
                    self.after(0, self._show_messagebox, 'warning', "Batch Loading Error", f"{error_message}\nSkipping batch.")
                    processed_page_count_in_run += (batch_end_page - batch_start_page + 1)
                    if not was_cancelled:
                         error_text = f"\n--- ERROR LOADING BATCH: Pages {batch_start_page}-{batch_end_page} ---\nError: {e}\n"
                         self.after(0, self._append_text_to_results, "", error_text)
                    continue # Skip processing

                # --- Process Pages in Batch ---
                batch_results_list = []
                batch_had_processing_errors = False
                for i, pil_image in enumerate(current_batch_images):
                    if cancel_event.is_set(): print("[INFO] [Thread] Cancel detected before page proc."); was_cancelled = True; final_status = "OCR cancelled."; break

                    current_actual_page_num = batch_start_page + i
                    processed_page_count_in_run += 1
                    self.after(0, self._update_status, f"OCR processing page {current_actual_page_num} ({processed_page_count_in_run}/{total_pages_to_process_in_run})...")

                    page_text = ""
                    try:
                        if cancel_event.is_set(): print("[INFO] [Thread] Cancel detected before ocr_page."); was_cancelled = True; final_status = "OCR cancelled."; break
                        prediction_result = ocr_page(pil_image, model_name=default_model) # Blocking call
                        if cancel_event.is_set(): print("[INFO] [Thread] Cancel detected after ocr_page."); was_cancelled = True; final_status = "OCR cancelled."; break

                        # Extract text...
                        if isinstance(prediction_result, str) and prediction_result.startswith("Error:"): page_text = prediction_result; batch_had_processing_errors = True
                        elif isinstance(prediction_result, str): page_text = prediction_result
                        elif hasattr(prediction_result, 'content') and isinstance(prediction_result.content, str): page_text = prediction_result.content
                        elif hasattr(prediction_result, 'text') and isinstance(prediction_result.text, str): page_text = prediction_result.text
                        elif hasattr(prediction_result, 'choices') and prediction_result.choices:
                            try:
                                first_choice = prediction_result.choices[0]; message = getattr(first_choice, 'message', None); content = getattr(message, 'content', None)
                                if content: page_text = content
                                elif hasattr(first_choice, 'text'): page_text = first_choice.text
                                else: page_text = str(first_choice); print(f"[WARN] Unexp. choice struct p{current_actual_page_num}")
                            except Exception as choice_e: page_text = f"Err parse choices: {choice_e}"; print(f"[ERROR] Parse choices p{current_actual_page_num}: {choice_e}"); batch_had_processing_errors = True
                        else: page_text = str(prediction_result); print(f"[WARN] Unknown OCR result p{current_actual_page_num}")

                        # Cleanup...
                        if not page_text.startswith("Error:"):
                            page_text = page_text.strip();
                            if page_text.startswith("```text"): page_text = page_text[len("```text"):].strip()
                            if page_text.startswith("```"): page_text = page_text[3:].strip()
                            if page_text.endswith("```"): page_text = page_text[:-3].strip()

                    except Exception as ex:
                        if cancel_event.is_set(): print("[INFO] [Thread] Cancel detected during page proc err."); was_cancelled = True; final_status = "OCR cancelled."; break
                        print(f"[ERROR] [Thread] OCR/proc failed p{current_actual_page_num}: {ex}"); traceback.print_exc()
                        page_text = f"Error processing page {current_actual_page_num}: {ex}"
                        batch_had_processing_errors = True

                    batch_results_list.append(f"--- Page {current_actual_page_num} ---\n{page_text}\n")
                    # --- End inner page loop ---

                # --- Append Batch Results ONLY IF NOT CANCELLED ---
                if not was_cancelled and batch_results_list:
                    batch_output_text = "\n".join(batch_results_list)
                    # Determine prefix based on whether results_text already has content
                    # Using get check within after() lambda for better thread safety on check
                    self.after(0, lambda bt=batch_output_text: self._append_text_to_results("\n" if self.results_text.get("1.0", "end-1c").strip() else "", bt) )
                    if not batch_had_processing_errors: any_batch_processed_successfully = True
                    print(f"[INFO] [Thread] Scheduled append batch {batch_start_page}-{batch_end_page}.")

                del current_batch_images
                del batch_results_list
                if was_cancelled: break # Exit outer loop if inner loop broke due to cancel
                # --- End outer batch loop ---

            if not was_cancelled: # Set final status only if process finished naturally
                final_status = "OCR process completed."
                if not any_batch_processed_successfully:
                    final_status = "OCR finished, but encountered errors or no text was processed."

        except Exception as e:
            print(f"[FATAL ERROR] [Thread] Unexpected error in OCR worker: {e}"); traceback.print_exc()
            final_status = f"OCR process failed unexpectedly: {e}"
            self.after(0, self._show_messagebox, 'error', "Worker Thread Error", f"An unexpected error occurred: {e}")

        finally:
            # --- Final GUI Updates via Main Thread ---
            print(f"[INFO] [Thread] OCR worker finished. Cancelled={was_cancelled}, Status='{final_status}'")
            self.after(0, self._update_status, final_status)
            # Re-enable controls based on whether PDF is still loaded
            pdf_loaded = self.pdf_file and self.total_pages > 0
            self.after(0, self._set_ocr_initiation_controls_state, tk.NORMAL if pdf_loaded else tk.DISABLED)
            self.after(0, self._set_navigation_state, tk.NORMAL if pdf_loaded else tk.DISABLED)
            self.after(0, self._set_cancel_button_state, tk.DISABLED)
            self.after(0, self._set_save_button_state, tk.NORMAL) # Save always enabled if window exists
            self.after(0, self._set_button_state, self.go_to_page_entry, tk.NORMAL if pdf_loaded else tk.DISABLED)
            self.after(0, self._set_button_state, self.go_button, tk.NORMAL if pdf_loaded else tk.DISABLED)

            # Re-check prev/next button states based on current index (safer with lambda)
            if self.winfo_exists():
                 self.after(0, lambda: self._set_button_state(self.prev_button, tk.NORMAL if pdf_loaded and self.current_page_index > 0 else tk.DISABLED))
                 self.after(0, lambda: self._set_button_state(self.next_button, tk.NORMAL if pdf_loaded and self.current_page_index < self.total_pages - 1 else tk.DISABLED))


    def _prompt_save(self):
        """Prompts the user to save the results currently in the text box."""
        if not self.results_text.winfo_exists(): return
        full_output_text_to_save = self.results_text.get("1.0", tk.END).strip()
        if not full_output_text_to_save:
            self._show_messagebox('info', "Save Results", "There is no text content to save.")
            return

        output_file = filedialog.asksaveasfilename(
            title="Save OCR Output", defaultextension=".txt", filetypes=[("Text files", "*.txt")], parent=self
        )
        if output_file:
            try:
                with open(output_file, "w", encoding="utf-8") as f: f.write(full_output_text_to_save)
                self._show_messagebox('info', "Save Successful", f"OCR text saved to:\n{output_file}")
                if not (self.ocr_thread and self.ocr_thread.is_alive()): self._update_status("Results saved.")
            except Exception as e:
                self._show_messagebox('error', "Save Error", f"Could not save OCR output: {e}")
                if not (self.ocr_thread and self.ocr_thread.is_alive()): self._update_status("Error saving file.")
        else:
             pass # User cancelled save dialog


    def on_closing(self):
        """Handle window close event."""
        if self.ocr_thread and self.ocr_thread.is_alive():
             print("[WARN] OCR thread is still running. Requesting cancellation before closing.")
             if self.cancel_event: self.cancel_event.set()
        print("Closing application window.")
        self.quit()
        self.destroy()


# --- Main Execution ---
def main():
    """Sets up and runs the Tkinter application."""
    try:
        import lmstudio
        print(f"LM Studio SDK found (version: {getattr(lmstudio, '__version__', 'unknown')}).")
    except ImportError:
        print("\n[ERROR] LM Studio Python SDK not found. Please install: pip install lmstudio\n")
        try: root = tk.Tk(); root.withdraw(); messagebox.showerror("Missing Dependency", "LM Studio Python SDK not found.\nPlease install it:\n\npip install lmstudio", parent=None); root.destroy()
        except Exception: pass
        sys.exit(1)

    try:
        app = PDFPreviewer()
        app.mainloop()
    except Exception as e:
        print(f"[FATAL APP ERROR] An error occurred: {e}"); traceback.print_exc()
        try: root = tk.Tk(); root.withdraw(); messagebox.showerror("Application Error", f"A critical error occurred:\n{e}", parent=None); root.destroy()
        except Exception: pass

if __name__ == "__main__":
    main()
