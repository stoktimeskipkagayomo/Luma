"""
User Dashboard Server - Token Usage Viewer
Port 5102
"""

import os
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from modules.token_manager import token_manager

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    # Initialize database
    await token_manager.initialize_database()
    
    logger.info("="*60)
    logger.info("📊 User Dashboard Server Starting")
    logger.info(f"   - Port: {os.getenv('USER_PORT', 5102)}")
    logger.info(f"   - Database: {token_manager.db_path}")
    logger.info("="*60)
    
    yield
    
    logger.info("User Dashboard Server shutting down")

app = FastAPI(lifespan=lifespan, title="Luma API User Dashboard")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/", response_class=HTMLResponse)
async def user_dashboard():
    """Serve user dashboard HTML at root path"""
    try:
        with open('templates/user_dashboard.html', 'r', encoding='utf-8') as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(
            content="<h1>User Dashboard</h1><p>Dashboard template not found. Please ensure templates/user_dashboard.html exists.</p>",
            status_code=404
        )

@app.get("/api/user/token-stats")
async def get_user_token_stats(token: str):
    """Get user token statistics (no auth required, validated by token itself)"""
    try:
        # Validate token
        token_info = await token_manager.get_token_by_value(token)
        if not token_info:
            raise HTTPException(status_code=404, detail="Token not found")
        
        # Get statistics
        stats = await token_manager.get_token_stats(token_info['id'])
        recent_usage = await token_manager.get_recent_usage(token_info['id'], limit=50)
        
        return {
            "token_info": token_info,
            "stats": stats,
            "recent_usage": recent_usage
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user token stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    port = int(os.getenv('USER_PORT', 5102))
    uvicorn.run(app, host="0.0.0.0", port=port)
