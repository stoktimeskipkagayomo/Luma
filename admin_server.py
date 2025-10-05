"""
Admin Dashboard Server - User Token Management
Port 5103
"""

import os
import logging
import time
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, Request, HTTPException, Depends, status, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from dotenv import load_dotenv

from modules.token_manager import token_manager
from modules.geo_platform import geo_platform_service

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# JWT Configuration
SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'your-secret-key-here')
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = int(os.getenv('TOKEN_EXPIRY_DAYS', 30))

security = HTTPBearer()

# WebSocket clients for real-time updates
monitor_clients = set()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    # Initialize database
    await token_manager.initialize_database()
    
    # Ensure admin user exists from .env
    await token_manager.ensure_admin_exists()
    
    logger.info("="*60)
    logger.info("🔐 Admin Dashboard Server Starting")
    logger.info(f"   - Port: {os.getenv('ADMIN_PORT', 5103)}")
    logger.info(f"   - Admin Username: {os.getenv('ADMIN_USERNAME', 'admin')}")
    logger.info(f"   - Database: {token_manager.db_path}")
    logger.info("="*60)
    
    yield
    
    # Cleanup
    await geo_platform_service.close()
    logger.info("Admin Dashboard Server shutting down")

app = FastAPI(lifespan=lifespan, title="Luma API Admin Dashboard")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Helper functions
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_admin(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verify JWT token and return admin username"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        return username
    except JWTError:
        raise credentials_exception

# API Endpoints

@app.post("/api/auth/login")
async def login(request: Request):
    """Admin login endpoint"""
    try:
        data = await request.json()
        username = data.get("username")
        password = data.get("password")
        
        if not username or not password:
            raise HTTPException(status_code=400, detail="Username and password required")
        
        # Verify credentials
        if await token_manager.verify_admin(username, password):
            # Create access token
            access_token = create_access_token(data={"sub": username})
            return {
                "access_token": access_token,
                "token_type": "bearer",
                "username": username
            }
        else:
            raise HTTPException(status_code=401, detail="Invalid credentials")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/auth/verify")
async def verify_token(admin: str = Depends(get_current_admin)):
    """Verify if token is valid"""
    return {"valid": True, "username": admin}

@app.get("/api/stats/overview")
async def get_overview_stats(admin: str = Depends(get_current_admin)):
    """Get overview statistics"""
    stats = await token_manager.get_all_stats()
    return stats

@app.get("/api/tokens")
async def list_tokens(admin: str = Depends(get_current_admin)):
    """List all tokens with basic stats"""
    tokens = await token_manager.list_tokens()
    
    # Add basic stats for each token
    for token in tokens:
        stats = await token_manager.get_token_stats(token['id'])
        token['stats'] = stats
    
    return tokens

@app.post("/api/tokens/create")
async def create_token(request: Request, admin: str = Depends(get_current_admin)):
    """Create a new token"""
    try:
        data = await request.json()
        user_id = data.get("user_id")
        name = data.get("name")
        notes = data.get("notes")
        
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id is required")
        
        token, token_id = await token_manager.create_token(
            user_id=user_id,
            name=name,
            created_by_admin=admin,
            notes=notes
        )
        
        # Broadcast update to WebSocket clients
        await broadcast_update({"type": "token_created", "token_id": token_id})
        
        return {
            "success": True,
            "token": token,
            "token_id": token_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating token: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/tokens/{token_id}/status")
async def update_token_status(token_id: int, request: Request, admin: str = Depends(get_current_admin)):
    """Enable or disable a token"""
    try:
        data = await request.json()
        enabled = data.get("enabled", True)
        
        await token_manager.update_token_status(token_id, enabled)
        
        # Broadcast update
        await broadcast_update({"type": "token_updated", "token_id": token_id})
        
        return {"success": True}
        
    except Exception as e:
        logger.error(f"Error updating token status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/tokens/{token_id}")
async def delete_token(token_id: int, admin: str = Depends(get_current_admin)):
    """Delete a token"""
    try:
        await token_manager.delete_token(token_id)
        
        # Broadcast update
        await broadcast_update({"type": "token_deleted", "token_id": token_id})
        
        return {"success": True}
        
    except Exception as e:
        logger.error(f"Error deleting token: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/tokens/{token_id}/stats")
async def get_token_stats(token_id: int, admin: str = Depends(get_current_admin)):
    """Get detailed statistics for a token"""
    try:
        token_info = await token_manager.get_token_by_id(token_id)
        if not token_info:
            raise HTTPException(status_code=404, detail="Token not found")
        
        stats = await token_manager.get_token_stats(token_id)
        recent_usage = await token_manager.get_recent_usage(token_id, limit=100)
        
        return {
            "token_info": token_info,
            "stats": stats,
            "recent_usage": recent_usage
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting token stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/usage/recent")
async def get_recent_usage(limit: int = 100, admin: str = Depends(get_current_admin)):
    """Get recent usage across all tokens"""
    try:
        # Get all tokens
        tokens = await token_manager.list_tokens()
        
        # Collect recent usage from all tokens
        all_usage = []
        for token in tokens:
            usage = await token_manager.get_recent_usage(token['id'], limit=limit)
            for u in usage:
                u['user_id'] = token['user_id']
                u['token_name'] = token.get('name', 'Unnamed')
            all_usage.extend(usage)
        
        # Sort by timestamp
        all_usage.sort(key=lambda x: x['timestamp'], reverse=True)
        
        return all_usage[:limit]
        
    except Exception as e:
        logger.error(f"Error getting recent usage: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/usage/geographic")
async def get_geographic_usage(admin: str = Depends(get_current_admin)):
    """Get geographic usage statistics for all users"""
    try:
        stats = await token_manager.get_all_geographic_stats()
        return stats
        
    except Exception as e:
        logger.error(f"Error getting geographic usage: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# WebSocket for real-time updates
@app.websocket("/ws/updates")
async def websocket_updates(websocket: WebSocket):
    """WebSocket endpoint for real-time dashboard updates"""
    await websocket.accept()
    monitor_clients.add(websocket)
    
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        monitor_clients.discard(websocket)

async def broadcast_update(data: dict):
    """Broadcast update to all connected WebSocket clients"""
    disconnected = []
    for client in monitor_clients:
        try:
            await client.send_json(data)
        except:
            disconnected.append(client)
    
    # Clean up disconnected clients
    for client in disconnected:
        monitor_clients.discard(client)

# HTML Pages
@app.get("/", response_class=HTMLResponse)
async def admin_dashboard():
    """Serve admin dashboard HTML"""
    try:
        with open('templates/admin_dashboard.html', 'r', encoding='utf-8') as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(
            content="<h1>Admin Dashboard</h1><p>Dashboard template not found. Please ensure templates/admin_dashboard.html exists.</p>",
            status_code=404
        )

@app.get("/login", response_class=HTMLResponse)
async def login_page():
    """Serve login page HTML"""
    try:
        with open('templates/admin_login.html', 'r', encoding='utf-8') as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(
            content="<h1>Login</h1><p>Login template not found. Please ensure templates/admin_login.html exists.</p>",
            status_code=404
        )

if __name__ == "__main__":
    port = int(os.getenv('ADMIN_PORT', 5103))
    uvicorn.run(app, host="0.0.0.0", port=port)
