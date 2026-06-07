import requests
import json

res = requests.post("http://localhost:8000/connection-details", json={"company_key":"test", "modality":"voice"})
print("/connection-details status:", res.status_code)
print(res.text)

res2 = requests.post("http://localhost:8000/api/chat", json={"company_key":"test", "query":"hello", "product_id":"123"})
print("/api/chat status:", res2.status_code)
print(res2.text)
