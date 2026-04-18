from flask import current_app
import requests
from typing import Dict, List, Optional, Tuple
import json


class GoogleMapsService:
    """Service for Google Maps API operations"""
    
    def __init__(self):
        self.api_key = None
        self.base_url = "https://maps.googleapis.com/maps/api"
        self._configure()
    
    def _configure(self):
        """Configure Google Maps API"""
        try:
            self.api_key = current_app.config.get('GOOGLE_MAPS_API_KEY')
            if not self.api_key:
                current_app.logger.warning("GOOGLE_MAPS_API_KEY not configured")
        except Exception as e:
            current_app.logger.error(f"Error configuring Google Maps: {str(e)}")
    
    def get_maps_url(self, query: str) -> str:
        """
        Generate a Google Maps URL for a search query
        
        Args:
            query: Location name or address
            
        Returns:
            Google Maps URL string (Free)
        """
        from urllib.parse import quote
        return f"https://www.google.com/maps/search/?api=1&query={quote(query)}"

    def get_directions_url(self, destination: str, origin: Optional[str] = None, mode: str = 'driving') -> str:
        """
        Generate a Google Maps Directions URL (Free)
        
        Args:
            destination: Destination name or address
            origin: Optional starting point (address). If None, defaults to user's current location.
            mode: travelmode (driving, walking, bicycling, transit)
            
        Returns:
            Google Maps Directions URL string
        """
        from urllib.parse import quote
        url = f"https://www.google.com/maps/dir/?api=1&destination={quote(destination)}&travelmode={mode}"
        if origin:
            url += f"&origin={quote(origin)}"
        return url

    def get_navigation_url(self, query: str) -> str:
        """
        Generate a Google Maps Navigation URL for mobile devices (Free)
        """
        from urllib.parse import quote
        return f"google.navigation:q={quote(query)}"

    # Legacy methods marked as deprecated or simplified to avoid costs
    def geocode(self, address: str) -> Dict:
        """Geocoding still needs API key if used server-side, but we favor client-side search"""
        return {'success': False, 'error': 'Use client-side URL schemes for free usage'}

    def get_directions(self, *args, **kwargs) -> Dict:
        return {'success': False, 'error': 'API deprecated for cost saving. Use get_directions_url instead.'}


# Singleton instance
_maps_service = None

def get_maps_service() -> GoogleMapsService:
    """
    Get Google Maps service instance
    
    Returns:
        GoogleMapsService singleton instance
    """
    global _maps_service
    if _maps_service is None:
        _maps_service = GoogleMapsService()
    return _maps_service