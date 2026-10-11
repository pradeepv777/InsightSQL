import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_api_health():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ["healthy", "degraded"]

def test_create_conversation_and_chat():
    # 1. Create conversation
    conv_res = client.post("/api/v1/conversations")
    assert conv_res.status_code == 200
    conv_id = conv_res.json()["id"]
    
    # 2. Upload CSV
    csv_content = b"item,amount\nA,10\nB,20\n"
    up_res = client.post(
        "/api/v1/datasets",
        files={"file": ("test_sales.csv", csv_content, "text/csv")}
    )
    assert up_res.status_code == 200
    ds_id = up_res.json()["id"]
    
    # 3. Post analytical message
    msg_res = client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"message": "How many rows and columns does this dataset have?", "dataset_ids": [ds_id]}
    )
    assert msg_res.status_code == 200
    msg_data = msg_res.json()
    assert msg_data["status"] == "success"
    assert "2 rows" in msg_data["answer"]
    
    # 4. Check conversation history
    hist_res = client.get(f"/api/v1/conversations/{conv_id}/messages")
    assert hist_res.status_code == 200
    assert len(hist_res.json()) >= 2
