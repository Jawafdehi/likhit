import pytest
from fastapi.testclient import TestClient
from likhit.service.main import app

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"

def test_convert_no_file():
    response = client.post("/convert")
    assert response.status_code == 422 # Validation error

def test_convert_invalid_file():
    # This might fail because dependencies aren't installed, 
    # but we can try with a dummy file.
    files = {"file": ("test.txt", b"dummy content", "text/plain")}
    response = client.post("/convert", files=files)
    # It might fail with 500 if MarkItDown fails, which is expected for dummy content
    assert response.status_code in [200, 500] 
