""" DocumentClass() implementation
  - Auto-detects document format (PDF, PPTX, TIFF, DOCX/ODT/RTF) and counts pages.
  - Manages OCR-extracted objects (text, images, tables) with unique indices and page refs.
  - Provides methods to add, retrieve, and delete objects by index or page.
"""

from dataclasses import dataclass, field
from typing import List, Optional
from pathlib import Path


class DocumentClass():

    def __init__(self):
        # "File type" refers to the file format of this document (DOCX, HTML, PDF...).
        self.file_type: Optional[str] = None
        self.objects = []
        self._obj_index: int = 0
        self._pages: int = 0

    def add_page(self):
        self._pages += 1
       
    def add_object(self, page: Optional[int], text, coordinates: Optional[List[int]] = None): 
        
        if coordinates is None:
            new_object = ObjectType(self.obj_index, page, text)
        else:
            new_object = ObjectType(self.obj_index, page, text, coordinates)

        self.objects.append(new_object)
        self._obj_index += 1

    def detect_type(self, file_path: str) -> None:
        # Auto-detects and sets self.file_type and self._pages
        # based on the file at file_path.

        ext = Path(file_path).suffix.lower()
        
        # PDF
        if ext == '.pdf':
            self.file_type = 'PDF'

            try:
                import pdf2image  # PDF2image
                from pdf2image import pdfinfo_from_path
                info = pdfinfo_from_path(file_path)
                self._pages = info.get("Pages", 0)
            except ImportError:
                 raise RuntimeError("Install pdf2image and Poppler to handle PDFs.")
        
        # PowerPoint
        elif ext == '.pptx':
            self.file_type = 'PPTX'
            try:
                from pptx import Presentation
                prs = Presentation(file_path)
                self._pages = len(prs.slides)
            except ImportError:
                raise RuntimeError("Install python-pptx to handle PPTX.")
        
        # Multi-page TIFF
        elif ext in ('.tif', '.tiff'):
            self.file_type = 'TIFF'
            try:
                from PIL import Image
                img = Image.open(file_path)
                self._pages = getattr(img, 'n_frames', 1)
            except ImportError:
                raise RuntimeError("Install Pillow to handle TIFF.")
        
        # DOCX / ODT / RTF (no native page count in XML)
        elif ext in ('.docx', '.odt', '.rtf'):
            self.file_type = ext.lstrip('.').upper()
            # Fallback: convert to PDF and count pages
            try:
                from docx2pdf import convert
                import fitz
                tmp_pdf = Path(file_path).with_suffix('.pdf')
                convert(file_path, tmp_pdf)
                doc = fitz.open(str(tmp_pdf))
                self._pages = doc.page_count
            except Exception:
                # Couldn’t convert/count—default to 0
                self._pages = 0
        
        # Anything else
        else:
            self.file_type = ext.lstrip('.').upper() or 'UNKNOWN'
            self._pages = 0

    # Prevents modification of object index.
    @property
    def obj_index(self) -> int:
        return self._obj_index

    # Prevents modification of total of document pages.
    @property
    def pages(self) -> int:
        return self._pages

    # Obtains an object based on its index. Returns 'None' if no object was found.
    def get_object(self, index: int) -> Optional[ObjectType]:

        for obj in self.objects:
            if obj.index == index:
                return obj
        return None
    
    # Removes the number of deleted objects.
    def delete_object(self, index: int) -> int:
        before = len(self.objects)

        for obj in self.objects:
            if obj.index == index:
                self.objects.remove(obj)

        return before - len(self.objects)

    # Deletes all objects matching a given page.
    def delete_page_objects(self, doc_page: int):
        # Will return zero if the document has no pages.
        if not self.has_pages():
            return 0

        self.objects = [
            obj for obj in self.objects
            if obj.page != doc_page 
        ]

    def has_pages(self)  -> bool:
        # Returns true if self.pages is greater than zero, or false otherwise.
        return self.pages > 0
    

