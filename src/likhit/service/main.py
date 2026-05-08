import io
import logging
import time
import os
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, status
from fastapi.responses import JSONResponse
from markitdown import MarkItDown
from likhit.service.logging_utils import setup_logging

setup_logging()
logger = logging.getLogger("likhit.service")

app = FastAPI(title="Likhit PDF Extraction Service")

# Memory protection: limit maximum file size (e.g., 50MB)
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", 50 * 1024 * 1024))

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": time.time()}

@app.post("/convert")
async def convert_document(
    file: UploadFile = File(...),
    pages: Optional[str] = Form(None),
):
    start_time = time.time()
    
    # Check file size
    file_size = 0
    content = await file.read()
    file_size = len(content)
    
    if file_size > MAX_FILE_SIZE:
        logger.error(f"File size too large: {file_size} bytes")
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size exceeds limit of {MAX_FILE_SIZE} bytes"
        )

    logger.info(f"Starting conversion for file: {file.filename}, size: {file_size} bytes")
    
    try:
        # Initialize MarkItDown with plugins enabled
        # This will pick up the NepaliPdfConverter
        md = MarkItDown(enable_plugins=True)
        
        # We need to write the content to a temporary location or use a stream
        # MarkItDown's convert method handles streams if the stream_info is provided
        # or it can take a file path.
        
        # Using a temporary file to be safe with all converters
        import tempfile
        from pathlib import Path
        
        suffix = Path(file.filename).suffix
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        
        try:
            convert_kwargs = {}
            if pages:
                convert_kwargs["pages"] = pages
                
            result = md.convert(tmp_path, **convert_kwargs)
            
            markdown = result.markdown or result.text_content
            
            duration = time.time() - start_time
            logger.info(f"Conversion successful for {file.filename} in {duration:.2f}s")
            
            return {
                "filename": file.filename,
                "file_size": file_size,
                "markdown": markdown,
                "duration_seconds": duration,
                "status": "success"
            }
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
                
    except Exception as e:
        duration = time.time() - start_time
        logger.exception(f"Conversion failed for {file.filename} after {duration:.2f}s")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "status": "error",
                "error_type": type(e).__name__,
                "message": str(e),
                "duration_seconds": duration
            }
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
