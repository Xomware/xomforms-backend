"""
XOMFORMS Constants
==================
Ported from xomify-backend's lambdas/common/constants.py convention:
values sourced from environment variables (set by Terraform at deploy time),
with safe local defaults for tests.
"""

import os

AWS_DEFAULT_REGION = 'us-east-1'
AWS_ACCOUNT_ID = os.environ.get('AWS_ACCOUNT_ID', '')
PRODUCT = 'xomforms'

RESPONSE_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization,X-Amz-Date,X-Api-Key,X-Amz-Security-Token",
    "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
    "Content-Type": "application/json",
}

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# ============================================
# DynamoDB
# ============================================
DYNAMODB_KMS_ALIAS = os.environ.get('DYNAMODB_KMS_ALIAS', '')
POLLS_TABLE_NAME = os.environ.get('POLLS_TABLE_NAME', '')
RESPONSES_TABLE_NAME = os.environ.get('RESPONSES_TABLE_NAME', '')

# GSI on xomforms-polls: PK creatorEmail, SK createdAt -- powers "my polls".
POLLS_CREATOR_INDEX = os.environ.get('POLLS_CREATOR_INDEX', 'creatorEmail-createdAt-index')

# ============================================
# Grid / poll config caps
# ============================================
# Bounds enforced in lambdas/common/models.py so a single response item
# (list of selected blockIds) stays comfortably under DynamoDB's 400 KB
# item-size limit even in the worst case (every block selected).
MAX_GRID_BLOCKS = 2000
MAX_DATE_RANGE_DAYS = 60
# granularity ("block size") is no longer a user control -- the duration +
# start-range scheduler fixes grid resolution at 15 minutes always (see
# DEFAULT_GRANULARITY_MINUTES). The tuple is retained because the LEGACY create
# shape (dayStart/dayEnd/granularity) is still accepted for back-compat, and 15
# remains a member so derived grids validate.
ALLOWED_GRANULARITY_MINUTES = (15, 30, 60)
# Fixed grid resolution for the duration + start-range model. The frontend no
# longer exposes "block size"; every windowed scheduler poll is derived at
# 15-minute steps and this constant is what we persist as granularityMinutes.
DEFAULT_GRANULARITY_MINUTES = 15

# Event length ("duration") is now first-class: selectable every 15 minutes
# from 15 up to 360 (6 hours).
MIN_EVENT_DURATION_MINUTES = 15
MAX_EVENT_DURATION_MINUTES = 360
EVENT_DURATION_STEP_MINUTES = 15

# ============================================
# Misc
# ============================================
XOMFORMS_URL = "https://xomforms.xomware.com"
