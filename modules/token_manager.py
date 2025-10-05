"""
Token Management Module - User token system for Luma API
"""

import secrets
import string
import sqlite3
import aiosqlite
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple
from pathlib import Path
import os
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

class TokenManager:
    """Manages user tokens and usage tracking"""
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.getenv('DATABASE_PATH', 'user_tokens.db')
        self.db_path = Path(self.db_path)
        
    async def initialize_database(self):
        """Initialize the database with required tables"""
        async with aiosqlite.connect(self.db_path) as db:
            # Create tokens table
            await db.execute('''
                CREATE TABLE IF NOT EXISTS tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token TEXT UNIQUE NOT NULL,
                    user_id TEXT NOT NULL,
                    name TEXT,
                    created_by_admin TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    enabled BOOLEAN DEFAULT 1,
                    notes TEXT
                )
            ''')
            
            # Create token usage table
            await db.execute('''
                CREATE TABLE IF NOT EXISTS token_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_id INTEGER,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ip_address TEXT,
                    country TEXT,
                    city TEXT,
                    platform TEXT,
                    user_agent TEXT,
                    model TEXT,
                    endpoint TEXT,
                    success BOOLEAN,
                    error_msg TEXT,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    duration REAL,
                    FOREIGN KEY (token_id) REFERENCES tokens(id)
                )
            ''')
            
            # Create admins table
            await db.execute('''
                CREATE TABLE IF NOT EXISTS admins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create indexes for better performance
            await db.execute('CREATE INDEX IF NOT EXISTS idx_token ON tokens(token)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_token_usage_token_id ON token_usage(token_id)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_token_usage_timestamp ON token_usage(timestamp)')
            
            await db.commit()
            logger.info(f"Database initialized at {self.db_path}")
    
    def generate_token(self) -> str:
        """Generate a secure token with luma_ prefix"""
        # Generate 32 random alphanumeric characters
        alphabet = string.ascii_lowercase + string.digits
        random_part = ''.join(secrets.choice(alphabet) for _ in range(32))
        
        # Add luma_ prefix
        return f"luma_{random_part}"
    
    async def create_token(self, user_id: str, name: str = None, 
                          created_by_admin: str = None, notes: str = None) -> Tuple[str, int]:
        """Create a new user token"""
        token = self.generate_token()
        
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                '''INSERT INTO tokens (token, user_id, name, created_by_admin, notes)
                   VALUES (?, ?, ?, ?, ?)''',
                (token, user_id, name, created_by_admin, notes)
            )
            await db.commit()
            token_id = cursor.lastrowid
            
        logger.info(f"Created token for user {user_id}: {token[:15]}...")
        return token, token_id
    
    async def validate_token(self, token: str) -> Optional[Dict]:
        """Validate a token and return token info if valid"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                'SELECT * FROM tokens WHERE token = ? AND enabled = 1',
                (token,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)
        return None
    
    async def get_token_by_id(self, token_id: int) -> Optional[Dict]:
        """Get token information by ID"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                'SELECT * FROM tokens WHERE id = ?',
                (token_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)
        return None
    
    async def get_token_by_value(self, token: str) -> Optional[Dict]:
        """Get token information by token value"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                'SELECT * FROM tokens WHERE token = ?',
                (token,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)
        return None
    
    async def list_tokens(self, enabled_only: bool = False) -> List[Dict]:
        """List all tokens"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            query = 'SELECT * FROM tokens'
            if enabled_only:
                query += ' WHERE enabled = 1'
            query += ' ORDER BY created_at DESC'
            
            async with db.execute(query) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
    
    async def update_token_status(self, token_id: int, enabled: bool) -> bool:
        """Enable or disable a token"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                'UPDATE tokens SET enabled = ? WHERE id = ?',
                (1 if enabled else 0, token_id)
            )
            await db.commit()
        
        logger.info(f"Token {token_id} {'enabled' if enabled else 'disabled'}")
        return True
    
    async def delete_token(self, token_id: int) -> bool:
        """Delete a token and its usage history"""
        async with aiosqlite.connect(self.db_path) as db:
            # Delete usage history first
            await db.execute('DELETE FROM token_usage WHERE token_id = ?', (token_id,))
            # Delete token
            await db.execute('DELETE FROM tokens WHERE id = ?', (token_id,))
            await db.commit()
        
        logger.info(f"Deleted token {token_id}")
        return True
    
    async def log_usage(self, token_id: int, ip_address: str, user_agent: str,
                       model: str, endpoint: str, success: bool,
                       error_msg: str = None, input_tokens: int = 0,
                       output_tokens: int = 0, duration: float = None,
                       country: str = None, city: str = None, platform: str = None):
        """Log token usage"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                '''INSERT INTO token_usage 
                   (token_id, ip_address, country, city, platform, user_agent, 
                    model, endpoint, success, error_msg, input_tokens, output_tokens, duration)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (token_id, ip_address, country, city, platform, user_agent,
                 model, endpoint, 1 if success else 0, error_msg,
                 input_tokens, output_tokens, duration)
            )
            await db.commit()
    
    async def get_token_stats(self, token_id: int) -> Dict:
        """Get usage statistics for a token"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            
            # Total requests
            async with db.execute(
                'SELECT COUNT(*) as total FROM token_usage WHERE token_id = ?',
                (token_id,)
            ) as cursor:
                row = await cursor.fetchone()
                total_requests = row['total']
            
            # Successful requests
            async with db.execute(
                'SELECT COUNT(*) as success FROM token_usage WHERE token_id = ? AND success = 1',
                (token_id,)
            ) as cursor:
                row = await cursor.fetchone()
                successful_requests = row['success']
            
            # Failed requests
            failed_requests = total_requests - successful_requests
            
            # Last used
            async with db.execute(
                'SELECT MAX(timestamp) as last_used FROM token_usage WHERE token_id = ?',
                (token_id,)
            ) as cursor:
                row = await cursor.fetchone()
                last_used = row['last_used']
            
            # Platform breakdown
            async with db.execute(
                '''SELECT platform, COUNT(*) as count 
                   FROM token_usage 
                   WHERE token_id = ? AND platform IS NOT NULL
                   GROUP BY platform
                   ORDER BY count DESC''',
                (token_id,)
            ) as cursor:
                platforms = await cursor.fetchall()
                platform_breakdown = {row['platform']: row['count'] for row in platforms}
            
            # Geographic breakdown
            async with db.execute(
                '''SELECT country, city, COUNT(*) as count 
                   FROM token_usage 
                   WHERE token_id = ? AND country IS NOT NULL
                   GROUP BY country, city
                   ORDER BY count DESC
                   LIMIT 10''',
                (token_id,)
            ) as cursor:
                locations = await cursor.fetchall()
                location_breakdown = [
                    {'country': row['country'], 'city': row['city'], 'count': row['count']}
                    for row in locations
                ]
            
            # Token usage
            async with db.execute(
                '''SELECT SUM(input_tokens) as total_input, SUM(output_tokens) as total_output
                   FROM token_usage WHERE token_id = ?''',
                (token_id,)
            ) as cursor:
                row = await cursor.fetchone()
                total_input_tokens = row['total_input'] or 0
                total_output_tokens = row['total_output'] or 0
            
            return {
                'total_requests': total_requests,
                'successful_requests': successful_requests,
                'failed_requests': failed_requests,
                'last_used': last_used,
                'platform_breakdown': platform_breakdown,
                'location_breakdown': location_breakdown,
                'total_input_tokens': total_input_tokens,
                'total_output_tokens': total_output_tokens
            }
    
    async def get_recent_usage(self, token_id: int, limit: int = 50) -> List[Dict]:
        """Get recent usage logs for a token"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                '''SELECT * FROM token_usage 
                   WHERE token_id = ? 
                   ORDER BY timestamp DESC 
                   LIMIT ?''',
                (token_id, limit)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
    
    async def get_all_stats(self) -> Dict:
        """Get overall statistics for all tokens"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            
            # Total tokens
            async with db.execute('SELECT COUNT(*) as total FROM tokens') as cursor:
                row = await cursor.fetchone()
                total_tokens = row['total']
            
            # Active tokens
            async with db.execute('SELECT COUNT(*) as active FROM tokens WHERE enabled = 1') as cursor:
                row = await cursor.fetchone()
                active_tokens = row['active']
            
            # Total requests
            async with db.execute('SELECT COUNT(*) as total FROM token_usage') as cursor:
                row = await cursor.fetchone()
                total_requests = row['total']
            
            # Requests today
            async with db.execute(
                "SELECT COUNT(*) as today FROM token_usage WHERE DATE(timestamp) = DATE('now')"
            ) as cursor:
                row = await cursor.fetchone()
                requests_today = row['today']
            
            # Requests this week
            async with db.execute(
                "SELECT COUNT(*) as week FROM token_usage WHERE timestamp >= DATE('now', '-7 days')"
            ) as cursor:
                row = await cursor.fetchone()
                requests_week = row['week']
            
            # Requests this month
            async with db.execute(
                "SELECT COUNT(*) as month FROM token_usage WHERE timestamp >= DATE('now', '-30 days')"
            ) as cursor:
                row = await cursor.fetchone()
                requests_month = row['month']
            
            return {
                'total_tokens': total_tokens,
                'active_tokens': active_tokens,
                'total_requests': total_requests,
                'requests_today': requests_today,
                'requests_week': requests_week,
                'requests_month': requests_month
            }
    
    async def get_all_geographic_stats(self) -> Dict:
        """Get geographic usage statistics across all tokens"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            
            # Country breakdown
            async with db.execute(
                '''SELECT country, COUNT(*) as count 
                   FROM token_usage 
                   WHERE country IS NOT NULL
                   GROUP BY country
                   ORDER BY count DESC
                   LIMIT 20''',
            ) as cursor:
                countries = await cursor.fetchall()
                country_breakdown = [
                    {'country': row['country'], 'count': row['count']}
                    for row in countries
                ]
            
            # City breakdown (top 20)
            async with db.execute(
                '''SELECT country, city, COUNT(*) as count 
                   FROM token_usage 
                   WHERE country IS NOT NULL AND city IS NOT NULL
                   GROUP BY country, city
                   ORDER BY count DESC
                   LIMIT 20''',
            ) as cursor:
                cities = await cursor.fetchall()
                city_breakdown = [
                    {'country': row['country'], 'city': row['city'], 'count': row['count']}
                    for row in cities
                ]
            
            return {
                'country_breakdown': country_breakdown,
                'city_breakdown': city_breakdown
            }
    
    # Admin management
    def hash_password(self, password: str) -> str:
        """Hash a password using SHA256"""
        return hashlib.sha256(password.encode()).hexdigest()
    
    async def create_admin(self, username: str, password: str) -> bool:
        """Create an admin user"""
        password_hash = self.hash_password(password)
        
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    'INSERT INTO admins (username, password_hash) VALUES (?, ?)',
                    (username, password_hash)
                )
                await db.commit()
            logger.info(f"Created admin user: {username}")
            return True
        except sqlite3.IntegrityError:
            logger.warning(f"Admin user {username} already exists")
            return False
    
    async def verify_admin(self, username: str, password: str) -> bool:
        """Verify admin credentials"""
        password_hash = self.hash_password(password)
        
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                'SELECT * FROM admins WHERE username = ? AND password_hash = ?',
                (username, password_hash)
            ) as cursor:
                row = await cursor.fetchone()
                return row is not None
    
    async def ensure_admin_exists(self):
        """Ensure admin user exists from .env credentials"""
        admin_username = os.getenv('ADMIN_USERNAME', 'admin')
        admin_password = os.getenv('ADMIN_PASSWORD', 'admin123')
        
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                'SELECT * FROM admins WHERE username = ?',
                (admin_username,)
            ) as cursor:
                row = await cursor.fetchone()
                
                if not row:
                    # Create admin from .env
                    await self.create_admin(admin_username, admin_password)
                    logger.info(f"Created admin user from .env: {admin_username}")
                else:
                    # Update password if changed in .env
                    password_hash = self.hash_password(admin_password)
                    await db.execute(
                        'UPDATE admins SET password_hash = ? WHERE username = ?',
                        (password_hash, admin_username)
                    )
                    await db.commit()
                    logger.info(f"Updated admin password from .env: {admin_username}")

# Global token manager instance
token_manager = TokenManager()
