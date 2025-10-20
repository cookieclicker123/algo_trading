# NewsFlash Trading System

A high-frequency news trading system that polls Polygon.io's Benzinga news API every 50ms to capture market-moving news in real-time.

## Features

- **Multi-Source News Feeds**: Supports multiple news sources simultaneously
  - **Benzinga**: Polygon.io HTTP polling (50ms intervals, 20 requests/second)
  - **Finlight.me**: Real-time WebSocket streaming
- **Standardized Data Format**: Unified article processing across all sources
- **Delta-based Deduplication**: Only processes new articles using ID-based filtering
- **Rolling Window Storage**: Maintains last hour of articles for immediate access
- **24-Hour Archiving**: Organizes historical data by date (year/month/week/date.json)
- **Source Identification**: Track which source provided each article for performance analysis
- **FastAPI Server**: REST API with health checks and statistics
- **Robust Error Handling**: Exponential backoff and automatic reconnection
- **Structured Logging**: Comprehensive logging for monitoring and debugging

## Prerequisites

- Python 3.9+
- Polygon.io API key with Benzinga add-on subscription
- Finlight.me API key (optional, for additional news source)
- `uv` package manager (recommended) or `pip`

## Quick Setup

### 1. Clone and Navigate
```bash
git clone <repository-url>
cd newsflash
```

### 2. Create Virtual Environment
```bash
# Using uv (recommended)
uv venv .venv
source .venv/bin/activate

# Or using venv
python -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies
```bash
# Using uv (recommended)
uv pip install -e .

# Or using pip
pip install -e .
```

### 4. Environment Setup
Create a `.env` file in the project root:
```bash
# Copy the example file
cp .env.example .env

# Edit with your API key
nano .env
```

Add your API keys:
```env
POLYGON_API_KEY=your_polygon_api_key_here
FINLIGHT_API_KEY=your_finlight_api_key_here

# Optional: Telegram notifications (Phase 1 - all articles, no filtering yet)
TELEGRAM_ENABLED=false
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_here
```

### 5. Optional: Configure Telegram Notifications

To receive news alerts on Telegram:

1. **Create a Bot**:
   - Open Telegram and message `@BotFather`
   - Send: `/newbot`
   - Follow the instructions to name your bot
   - Copy the bot token provided

2. **Get Your Chat ID**:
   - Message `@userinfobot` on Telegram
   - It will reply with your chat ID
   - Copy the numeric ID

3. **Update .env**:
   ```env
   TELEGRAM_ENABLED=true
   TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
   TELEGRAM_CHAT_ID=123456789
   ```

4. **Test Connection** (Optional):
   ```bash
   python -m tests.test_telegram_bot_connection
   ```
   
   This will send test messages to verify your bot is working correctly.

**Note**: Phase 1 sends ALL articles to Telegram without filtering. Phase 2 will add AI classification to only send high-signal news.

### 6. Verify Installation
```bash
# Run tests to verify everything works
pytest tests/ -v
```

## Usage

### Standalone Multi-Source System
Run the standalone multi-source news system:

```bash
# Make sure virtual environment is activated
source .venv/bin/activate

# Run standalone multi-source polling
python -m src.main
```

This will:
- Start Benzinga HTTP polling (50ms intervals)
- Start Finlight WebSocket streaming
- Store new articles from both sources in `tmp/articles.json`
- Archive articles older than 1 hour to dated files
- Log all activity to console with source identification

### FastAPI Server
Run the FastAPI server with integrated polling:

**Important**: Make sure to activate your virtual environment first!

```bash
# Activate virtual environment
source .venv/bin/activate

# Run server (using python -m to ensure correct environment)
python -m uvicorn src.server:app --host 127.0.0.1 --port 8000 --reload

# Alternative: Direct path to uvicorn in virtual environment
.venv/bin/uvicorn src.server:app --host 127.0.0.1 --port 8000 --reload
```

The server provides these endpoints:
- `GET /` - Service status
- `GET /health` - Health check
- `GET /stats` - System statistics
- `GET /recent-articles?hours=1` - Recent articles
- `GET /archived-articles/{date}` - Archived articles (YYYY-MM-DD)
- `GET /archive-stats` - Archive statistics
- `POST /start-polling` - Start polling manually
- `POST /stop-polling` - Stop polling manually

## Data Storage

### Current Articles (`tmp/articles.json`)
- Contains articles from the last hour
- Updated in real-time as new articles arrive
- Used for immediate analysis and processing

### Archived Articles (`tmp/archive/`)
Organized by date structure:
```
tmp/archive/
├── 2025/
│   ├── 01/
│   │   ├── week_01/
│   │   │   ├── 2025-01-01.json
│   │   │   ├── 2025-01-02.json
│   │   │   └── 2025-01-03.json
│   │   └── week_02/
│   │       └── 2025-01-04.json
│   └── 02/
│       └── ...
```

### Article Data Structure
```json
{
  "benzinga_id": 48032646,
  "author": "mohd haider",
  "published": "2025-10-05T02:04:27+00:00",
  "last_updated": "2025-10-05T02:04:28+00:00",
  "title": "Federal Judge Temporarily Blocks Trump's Deployment...",
  "teaser": "Judge Immergut grants temporary restraining order...",
  "body": "<p>Full article content in HTML...</p>",
  "url": "https://www.benzinga.com/news/politics/25/10/48032646/...",
  "images": ["https://..."],
  "channels": ["news", "politics"],
  "tickers": [],
  "tags": []
}
```

## Testing

### Run All Tests
```bash
pytest tests/ -v
```

### Run Specific Tests
```bash
# Test API connectivity
pytest tests/test_api_connection.py -v

# Test with coverage
pytest tests/ --cov=src/newsflash --cov-report=html
```

### Test API Connection
```bash
python -c "
import asyncio
from src.newsflash.services.news_poller import test_api_connection

async def test():
    result = await test_api_connection()
    print(f'API Connection: {result}')

asyncio.run(test())
"
```

## Development

### Code Formatting
```bash
# Format code
black src/ tests/

# Sort imports
isort src/ tests/

# Type checking
mypy src/
```

### Project Structure
```
newsflash/
├── src/newsflash/
│   ├── api/              # FastAPI application
│   ├── config/           # Configuration management
│   ├── models/           # Pydantic data models
│   ├── services/         # Core business logic
│   └── utils/            # Utility functions
├── tests/                # Test suite
├── tmp/                  # Data storage (git-ignored)
├── .env                  # Environment variables (git-ignored)
├── pyproject.toml        # Project configuration
└── README.md            # This file
```

## Configuration

### Environment Variables
- `POLYGON_API_KEY` - Your Polygon.io API key (required)
- `LOG_LEVEL` - Logging level (default: INFO)
- `POLLING_INTERVAL_MS` - Polling interval in milliseconds (default: 50)

### Storage Configuration
- Rolling window: 1 hour (configurable)
- Archive window: 24 hours (configurable)
- Archive structure: year/month/week/date.json

## Monitoring

### Health Checks
```bash
# Check if service is running
curl http://localhost:8000/health

# Get system statistics
curl http://localhost:8000/stats
```

### Logs
The system provides structured logging with:
- Article processing events
- Error handling and retries
- Performance metrics
- Archive operations

## Troubleshooting

### Common Issues

1. **API Key Not Found**
   ```
   ValueError: POLYGON_API_KEY not set
   ```
   Solution: Ensure `.env` file exists with valid API key

2. **Import Errors**
   ```
   ModuleNotFoundError: No module named 'structlog'
   ```
   Solution: 
   - Make sure you're in the activated virtual environment: `source .venv/bin/activate`
   - Install dependencies: `uv pip install -e .`
   - Use `python -m uvicorn` instead of just `uvicorn` to ensure correct Python environment

3. **Permission Errors**
   ```
   PermissionError: [Errno 13] Permission denied: 'tmp/'
   ```
   Solution: Ensure write permissions for `tmp/` directory

### Performance Tuning

- **Polling Interval**: Reduce for faster updates (minimum ~50ms due to API limits)
- **Archive Window**: Adjust based on storage requirements
- **Log Level**: Set to WARNING for production to reduce log volume

## API Rate Limits

- Polygon.io allows up to 100 requests/second
- System polls at 20 requests/second (50ms intervals)
- Built-in exponential backoff for rate limit handling

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Ensure all tests pass
6. Submit a pull request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

For issues and questions:
1. Check the troubleshooting section above
2. Review the logs for error details
3. Ensure your Polygon.io subscription includes Benzinga add-on
4. Verify API key permissions

---

**Note**: This system is designed for educational and research purposes. Always comply with your data provider's terms of service and applicable regulations when using for trading purposes.
