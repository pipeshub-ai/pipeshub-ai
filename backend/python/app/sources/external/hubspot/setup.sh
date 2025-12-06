#!/bin/bash
# HubSpot Integration Setup Script

set -e

echo "================================================"
echo "HubSpot Integration - Complete Setup"
echo "================================================"

# Check if we're in the right directory
if [ ! -f "hubspot_oauth.py" ]; then
    echo "❌ Error: Please run this script from the hubspot directory"
    echo "   cd app/sources/external/hubspot"
    exit 1
fi

# Step 1: Check Python version
echo ""
echo "📋 Step 1: Checking Python version..."
if command -v python3.11 &> /dev/null; then
    PYTHON_CMD="python3.11"
    echo "✅ Found Python 3.11"
elif command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
    echo "✅ Found Python 3"
else
    echo "❌ Python 3 not found. Please install Python 3.11+"
    exit 1
fi

# Step 2: Create/activate virtual environment
echo ""
echo "🔧 Step 2: Setting up virtual environment..."
if [ ! -d "../../../venv" ]; then
    echo "Creating virtual environment..."
    cd ../../..
    $PYTHON_CMD -m venv venv
    cd app/sources/external/hubspot
    echo "✅ Virtual environment created"
else
    echo "✅ Virtual environment already exists"
fi

# Activate venv
source ../../../venv/bin/activate
echo "✅ Virtual environment activated"

# Step 3: Install dependencies
echo ""
echo "📦 Step 3: Installing dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt
echo "✅ Dependencies installed"

# Step 4: Setup .env file
echo ""
echo "⚙️  Step 4: Configuring environment..."
if [ ! -f ".env" ]; then
    cp .env.template .env
    echo "✅ Created .env file from template"
    echo ""
    echo "📝 Please edit .env file and add your HubSpot credentials:"
    echo "   1. Go to https://developers.hubspot.com/"
    echo "   2. Create an app"
    echo "   3. Set redirect URL: http://localhost:8000/callback"
    echo "   4. Copy Client ID and Client Secret"
    echo "   5. Update .env file"
    echo ""
    read -p "Press Enter after you've updated .env file..."
else
    echo "✅ .env file already exists"
fi

# Step 5: Check if credentials are set
echo ""
echo "🔍 Step 5: Validating credentials..."
source .env

if [ -z "$HUBSPOT_CLIENT_ID" ] || [ "$HUBSPOT_CLIENT_ID" = "your_client_id_here" ]; then
    echo "⚠️  Warning: HUBSPOT_CLIENT_ID not set in .env"
    echo "Please update .env file with your credentials"
    exit 1
fi

if [ -z "$HUBSPOT_CLIENT_SECRET" ] || [ "$HUBSPOT_CLIENT_SECRET" = "your_client_secret_here" ]; then
    echo "⚠️  Warning: HUBSPOT_CLIENT_SECRET not set in .env"
    echo "Please update .env file with your credentials"
    exit 1
fi

echo "✅ Credentials configured"

# Step 6: Run OAuth flow
echo ""
echo "🔐 Step 6: Getting OAuth tokens..."
echo ""
echo "This will open your browser for authorization."
echo "Please authorize the app to continue."
echo ""
read -p "Press Enter to start OAuth flow..."

$PYTHON_CMD hubspot_oauth.py

# Step 7: Verify token
if [ -f "hubspot_token.txt" ]; then
    echo ""
    echo "================================================"
    echo "✅ Setup Complete!"
    echo "================================================"
    echo ""
    echo "📄 Your tokens are saved in:"
    echo "   - hubspot_token.txt"
    echo "   - .env file"
    echo ""
    echo "🧪 To test the integration, run:"
    echo "   export HUBSPOT_ACCESS_TOKEN=\"\$(grep 'Access Token:' hubspot_token.txt | cut -d' ' -f3)\""
    echo "   PYTHONPATH=../.. $PYTHON_CMD example.py"
    echo ""
else
    echo ""
    echo "⚠️  Token file not created. OAuth flow may have failed."
    echo "Please try running: $PYTHON_CMD hubspot_oauth.py"
fi
