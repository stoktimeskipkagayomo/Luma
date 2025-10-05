"""
Geolocation and Platform Detection Module
"""

import aiohttp
import logging
from typing import Optional, Dict, Tuple
from user_agents import parse
import os
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
load_dotenv()

class GeoPlatformService:
    """Service for IP geolocation and platform detection"""
    
    def __init__(self):
        self.geo_api_url = os.getenv('GEOLOCATION_API', 'http://ip-api.com/json/')
        self.session = None
    
    async def get_session(self):
        """Get or create aiohttp session"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session
    
    async def close(self):
        """Close aiohttp session"""
        if self.session and not self.session.closed:
            await self.session.close()
    
    async def get_location(self, ip_address: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Get country and city from IP address
        Returns: (country, city)
        """
        # Skip for localhost/private IPs
        if ip_address in ['127.0.0.1', 'localhost', '::1'] or ip_address.startswith('192.168.') or ip_address.startswith('10.'):
            return 'Local', 'Local'
        
        try:
            session = await self.get_session()
            url = f"{self.geo_api_url}{ip_address}"
            
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    if data.get('status') == 'success':
                        country = data.get('country', 'Unknown')
                        city = data.get('city', 'Unknown')
                        return country, city
                    else:
                        logger.warning(f"Geolocation API returned error for {ip_address}: {data.get('message')}")
                        return 'Unknown', 'Unknown'
                else:
                    logger.warning(f"Geolocation API returned status {response.status} for {ip_address}")
                    return 'Unknown', 'Unknown'
                    
        except asyncio.TimeoutError:
            logger.warning(f"Geolocation API timeout for {ip_address}")
            return 'Unknown', 'Unknown'
        except Exception as e:
            logger.error(f"Error getting location for {ip_address}: {e}")
            return 'Unknown', 'Unknown'
    
    def detect_platform(self, user_agent: str) -> str:
        """
        Detect platform/client from User-Agent string
        Returns: Platform name (e.g., 'Chrome on Windows', 'SillyTavern', 'Python/requests')
        """
        if not user_agent:
            return 'Unknown'
        
        try:
            # Check for common API clients first
            ua_lower = user_agent.lower()
            
            # API clients
            if 'sillytavern' in ua_lower:
                return 'SillyTavern'
            elif 'tavern' in ua_lower:
                return 'Tavern AI'
            elif 'postman' in ua_lower:
                return 'Postman'
            elif 'insomnia' in ua_lower:
                return 'Insomnia'
            elif 'python-requests' in ua_lower or 'python/requests' in ua_lower:
                return 'Python/requests'
            elif 'curl' in ua_lower:
                return 'cURL'
            elif 'httpx' in ua_lower:
                return 'Python/httpx'
            elif 'aiohttp' in ua_lower:
                return 'Python/aiohttp'
            elif 'axios' in ua_lower:
                return 'Node.js/Axios'
            elif 'node-fetch' in ua_lower or 'node.js' in ua_lower:
                return 'Node.js'
            
            # Parse browser user agents
            parsed = parse(user_agent)
            
            if parsed.is_bot:
                return f'Bot: {parsed.browser.family}'
            
            # Build platform string
            browser = parsed.browser.family
            browser_version = parsed.browser.version_string
            os_name = parsed.os.family
            os_version = parsed.os.version_string
            
            # Simplify common names
            if 'Windows' in os_name:
                os_name = 'Windows'
            elif 'Mac OS X' in os_name or 'macOS' in os_name:
                os_name = 'macOS'
            elif 'Linux' in os_name:
                os_name = 'Linux'
            elif 'Android' in os_name:
                os_name = 'Android'
            elif 'iOS' in os_name or 'iPhone' in os_name:
                os_name = 'iOS'
            
            # Build result
            if browser and browser != 'Other':
                if os_name and os_name != 'Other':
                    return f'{browser} on {os_name}'
                else:
                    return browser
            elif os_name and os_name != 'Other':
                return os_name
            else:
                # Fallback to raw user agent (truncated)
                return user_agent[:50] + ('...' if len(user_agent) > 50 else '')
                
        except Exception as e:
            logger.error(f"Error parsing user agent: {e}")
            return user_agent[:50] + ('...' if len(user_agent) > 50 else '')

# Global service instance
geo_platform_service = GeoPlatformService()

# Import asyncio for timeout
import asyncio
