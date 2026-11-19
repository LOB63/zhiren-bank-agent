import os
from app.config import ROOT

if __name__=="__main__":
    import uvicorn
    uvicorn.run("app.main:app",host=os.getenv("APP_HOST","127.0.0.1"),port=int(os.getenv("APP_PORT","8765")),reload=False)
