import asyncio
from fastapi.testclient import TestClient
from fpdf import FPDF
import io
import logging

logging.basicConfig(level=logging.INFO)

from src.api.main import app
from src.api.deps import get_auth
from src.api.routes.companies import get_uid_only
from src.auth import AuthContext

current_company_id = "test_company_123"

# Mock auth
def override_get_auth():
    return AuthContext(uid="test_user_123", company_id=current_company_id)

def override_get_uid_only():
    return "test_user_123"

app.dependency_overrides[get_auth] = override_get_auth
app.dependency_overrides[get_uid_only] = override_get_uid_only

def get_client():
    return TestClient(app)

def create_dummy_pdf():
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, txt="This is a test document for Unsiloed.", ln=True, align='C')
    pdf.cell(200, 10, txt="It has some text that can be extracted.", ln=True, align='C')
    # Use output with dest='S' to get string/bytes depending on fpdf version
    # fpdf2 returns bytearray for dest='S'
    return bytes(pdf.output(dest='S'))

def run_e2e():
    with get_client() as client:
        print("1. Bootstrapping company...")
        res = client.post("/api/companies/bootstrap", json={"name": "E2E Test Company"})
        assert res.status_code == 200, res.text
        global current_company_id
        current_company_id = res.json()["company_id"]
        print("Bootstrap success:", res.json())

        print("2. Creating product...")
        res = client.post("/api/products", json={"name": "Test Product", "brand": "E2E Brand", "aliases": []})
        assert res.status_code == 200, res.text
        product_id = res.json()["product_id"]
        print(f"Product created: {product_id}")

        print("3. Uploading dummy PDF to backend proxy...")
        pdf_bytes = create_dummy_pdf()
        res = client.post(
            f"/api/products/{product_id}/upload",
            files={"file": ("test_manual.pdf", pdf_bytes, "application/pdf")}
        )
        assert res.status_code == 200, res.text
        storage_path = res.json()["storage_path"]
        print(f"File uploaded to R2: {storage_path}")

        print("4. Registering docs...")
        res = client.post(
            f"/api/products/{product_id}/docs",
            json={
                "docs": [{"doc_id": "test_doc_1", "doc_type": "other", "storage_path": storage_path}],
                "photo_path": ""
            }
        )
        assert res.status_code == 200, res.text
        print("Docs registered:", res.json())

        print("5. Triggering process (this adds a background task)...")
        res = client.post(f"/api/products/{product_id}/process")
        assert res.status_code == 200, res.text
        print("Process triggered:", res.json())

        import time
        print("6. Polling status...")
        while True:
            res = client.get(f"/api/products/{product_id}/status")
            assert res.status_code == 200, res.text
            status_data = res.json()
            print("Status:", status_data)
            if status_data["status"] in ["ready", "error"]:
                break
            time.sleep(2)

if __name__ == "__main__":
    run_e2e()
