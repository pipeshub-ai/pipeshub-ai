google_drive_sync/  
│  
├── src/  
│ ├── **init**.py  
│ │  
│ ├── api/ # New directory for FastAPI components  
│ │ ├── **init**.py  
│ │ ├── routes.py # Webhook endpoints  
│ │ ├── middleware.py # Authentication & validation middleware  
│ │ └── setup.py # DI setup
│ │  
│ ├── config/  
│ │ ├── **init**.py  
│ │ ├── arangodb_schema.py # ArangoDB schema  
│ │ └── config.py # Configuration classes and constants  
│ │  
│ ├── core/  
│ │ ├── **init**.py  
│ │ ├── drive_service.py # Google Drive API interactions  
│ │ ├── arango_service.py # ArangoDB operations  
│ │ ├── kafka_service.py # Kafka service  
│ │ ├── sync_service.py # Main synchronization logic  
│ │ └── redis_service.py # Redis service  
│ │  
│ ├── models/  
│ │ ├── **init**.py  
│ │ ├── file.py # File-related data models  
│ │ ├── webhook.py # New: Webhook related models  
│ │ ├── change.py # New: Change notification models  
│ │ ├── work_unit.py # New: Work unit models  
│ │ └── permission.py # Permission-related data models  
│ │  
│ ├── utils/  
│ │ ├── **init**.py  
│ │ └── logger.py # Logging configuration  
│ ├── tasks/ # New: Celery tasks directory  
│ │ ├── **init**.py  
│ │ ├── celery_app.py # New: Celery application configuration  
│ │ └── sync_tasks.py # New: Sync-related Celery tasks  
│ │  
│ │── workers/ # New: Worker management  
│ │ ├── **init**.py  
│ │ ├── drive_worker.py # New: Worker implementation  
│ │ └── rate_limiter.py # New: Rate limiting implementation  
│ │  
│ └── handlers/  
│ ├── **init**.py  
│ ├── change_handler.py # Change tracking logic  
│ ├── webhook_handler.py # Webhook notification handler  
│ ├── signed_url.py # Signed URL generation  
│ └── permission_handler.py # Permission processing logic  
│  
├── logs/  
│
├── setup.py  
└── README.md \*\*\*\*
