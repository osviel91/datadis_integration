"""Constants for Datadis integration."""

DOMAIN = "datadis"
PLATFORMS = ["sensor", "button", "number"]

CONF_DISTRIBUTOR_CODE = "distributor_code"
CONF_UPDATE_INTERVAL = "update_interval_minutes"
CONF_QUERY_DAYS = "query_days"

DEFAULT_DISTRIBUTOR_CODE = ""
DEFAULT_UPDATE_INTERVAL_MINUTES = 60
DEFAULT_QUERY_DAYS = 35

MIN_UPDATE_INTERVAL_MINUTES = 15
MAX_UPDATE_INTERVAL_MINUTES = 240
MIN_QUERY_DAYS = 3
MAX_QUERY_DAYS = 90

TOKEN_URL = "https://datadis.es/nikola-auth/tokens/login"
API_PRIVATE_BASE = "https://datadis.es/api-private/api"
SUPPLIES_URL = f"{API_PRIVATE_BASE}/get-supplies"
CONSUMPTION_URL = f"{API_PRIVATE_BASE}/get-consumption-data"
MAX_POWER_URL = f"{API_PRIVATE_BASE}/get-max-power"
CONTRACT_DETAIL_URL = f"{API_PRIVATE_BASE}/get-contract-detail"

MEASUREMENT_TYPE_ELECTRICITY = "0"
POINT_TYPE_SUPPLY_POINT = "5"
