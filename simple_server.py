from fastapi import FastAPI
import uvicorn
app = FastAPI(title="Test Server")
@app.get("/")
def read_root():
    return {"status": "ok", "message": "Backend is working"}
@app.get("/health")
def health():
    return {"status": "healthy"}
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
