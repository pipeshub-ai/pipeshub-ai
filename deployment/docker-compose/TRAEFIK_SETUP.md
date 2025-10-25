# Traefik Reverse Proxy Setup for PipesHub

This guide explains how to deploy PipesHub with Traefik reverse proxy for secure HTTPS access.

## Overview

PipesHub is configured to work with Traefik as a reverse proxy, providing:
- Automatic SSL/TLS certificates via Let's Encrypt
- Secure HTTPS access to the platform
- Proper routing for public and internal services

## Services Exposed

Based on PipesHub's architecture and security best practices, only two services are exposed publicly:

### 1. Frontend (Port 3000)
- **Domain**: `pipeshub.benjaminsanders.com` (customize for your domain)
- **Purpose**: Main web UI for the PipesHub platform
- **Access**: Public - users access the platform through this interface

### 2. Connector Backend (Port 8088)
- **Domain**: `pipeshub-connector.benjaminsanders.com` (customize for your domain)
- **Purpose**: Webhook notifications and real-time sync events
- **Access**: Public - required for third-party integrations to send webhook events

### Internal Services (Not Exposed)

These services remain internal and are accessed only through the frontend:
- **Query Backend** (port 8000)
- **Indexing Backend** (port 8091)
- **Additional Service** (port 8081)

## Prerequisites

### 1. Traefik Installation

You need a running Traefik instance. Here's a basic Traefik setup:

```yaml
# traefik-docker-compose.yml
version: '3.8'

services:
  traefik:
    image: traefik:v2.10
    container_name: traefik
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    networks:
      - traefik-net
    ports:
      - "80:80"
      - "443:443"
    environment:
      - TRAEFIK_LOG_LEVEL=INFO
    command:
      - "--api.dashboard=true"
      - "--providers.docker=true"
      - "--providers.docker.exposedbydefault=false"
      - "--providers.docker.network=traefik-net"
      - "--entrypoints.web.address=:80"
      - "--entrypoints.websecure.address=:443"
      - "--entrypoints.web.http.redirections.entrypoint.to=websecure"
      - "--entrypoints.web.http.redirections.entrypoint.scheme=https"
      - "--certificatesresolvers.myresolver.acme.tlschallenge=true"
      - "--certificatesresolvers.myresolver.acme.email=your-email@example.com"
      - "--certificatesresolvers.myresolver.acme.storage=/letsencrypt/acme.json"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./letsencrypt:/letsencrypt

networks:
  traefik-net:
    external: true
```

### 2. Create Traefik Network

```bash
docker network create traefik-net
```

### 3. DNS Configuration

Point your domains to your server's IP address:
- `pipeshub.benjaminsanders.com` → Your Server IP
- `pipeshub-connector.benjaminsanders.com` → Your Server IP

You can verify DNS propagation:
```bash
nslookup pipeshub.benjaminsanders.com
nslookup pipeshub-connector.benjaminsanders.com
```

## Configuration Steps

### Step 1: Customize Domain Names

Edit both `docker-compose.prod.yml` and `docker-compose.dev.yml` to replace the domain names with your own:

```yaml
labels:
  # Frontend service
  - "traefik.http.routers.pipeshub-frontend.rule=Host(`your-domain.com`)"

  # Connector Backend service
  - "traefik.http.routers.pipeshub-connector.rule=Host(`your-connector-domain.com`)"
```

### Step 2: Configure Environment Variables

Create or update your `.env` file in the `deployment/docker-compose` directory:

```bash
# Core Settings
NODE_ENV=production
LOG_LEVEL=info
ALLOWED_ORIGINS=https://your-domain.com

# Security
SECRET_KEY=your_secure_random_secret_key_here

# Public URLs - IMPORTANT for webhooks and real-time sync
CONNECTOR_PUBLIC_BACKEND=https://your-connector-domain.com
FRONTEND_PUBLIC_URL=https://your-domain.com

# Database Passwords
ARANGO_PASSWORD=your_secure_arango_password
MONGO_USERNAME=admin
MONGO_PASSWORD=your_secure_mongo_password
REDIS_PASSWORD=your_secure_redis_password
QDRANT_API_KEY=your_secure_qdrant_api_key
```

**Important**:
- `CONNECTOR_PUBLIC_BACKEND` must be set to the public URL of your connector service for webhooks to work
- `FRONTEND_PUBLIC_URL` should be set to your frontend domain
- These URLs must be publicly accessible

### Step 3: Verify Traefik Labels

The docker-compose files include the following Traefik configuration:

```yaml
services:
  pipeshub-ai:
    networks:
      - default
      - traefik-net
    expose:
      - "3000"
      - "8088"
    labels:
      - "traefik.enable=true"
      - "traefik.docker.network=traefik-net"

      # Frontend service (port 3000)
      - "traefik.http.routers.pipeshub-frontend.rule=Host(`pipeshub.benjaminsanders.com`)"
      - "traefik.http.routers.pipeshub-frontend.entrypoints=websecure"
      - "traefik.http.routers.pipeshub-frontend.tls.certresolver=myresolver"
      - "traefik.http.routers.pipeshub-frontend.service=pipeshub-frontend"
      - "traefik.http.services.pipeshub-frontend.loadbalancer.server.port=3000"

      # Connector Backend service (port 8088)
      - "traefik.http.routers.pipeshub-connector.rule=Host(`pipeshub-connector.benjaminsanders.com`)"
      - "traefik.http.routers.pipeshub-connector.entrypoints=websecure"
      - "traefik.http.routers.pipeshub-connector.tls.certresolver=myresolver"
      - "traefik.http.routers.pipeshub-connector.service=pipeshub-connector"
      - "traefik.http.services.pipeshub-connector.loadbalancer.server.port=8088"
```

## Deployment

### Production Deployment

```bash
# Navigate to deployment directory
cd pipeshub-ai/deployment/docker-compose

# Ensure environment variables are set
cp env.template .env
# Edit .env with your values

# Start PipesHub with Traefik support
docker compose -f docker-compose.prod.yml -p pipeshub-ai up -d

# View logs
docker compose -f docker-compose.prod.yml -p pipeshub-ai logs -f pipeshub-ai

# Stop services
docker compose -f docker-compose.prod.yml -p pipeshub-ai down
```

### Development Deployment

```bash
# Navigate to deployment directory
cd pipeshub-ai/deployment/docker-compose

# Start PipesHub development build
docker compose -f docker-compose.dev.yml -p pipeshub-ai up --build -d

# View logs
docker compose -f docker-compose.dev.yml -p pipeshub-ai logs -f pipeshub-ai

# Stop services
docker compose -f docker-compose.dev.yml -p pipeshub-ai down
```

## Verification

### 1. Check Container Status

```bash
docker ps | grep pipeshub-ai
```

### 2. Verify Traefik Routing

```bash
# Check Traefik logs
docker logs traefik

# Access Traefik dashboard (if enabled)
# https://traefik.your-domain.com/dashboard/
```

### 3. Test Access

```bash
# Test frontend
curl -I https://pipeshub.benjaminsanders.com

# Test connector backend
curl -I https://pipeshub-connector.benjaminsanders.com

# Both should return HTTP 200 or appropriate response
```

### 4. Verify SSL Certificates

```bash
# Check certificate details
echo | openssl s_client -connect pipeshub.benjaminsanders.com:443 -servername pipeshub.benjaminsanders.com 2>/dev/null | openssl x509 -noout -dates -issuer

# Should show Let's Encrypt as the issuer
```

## Troubleshooting

### Issue: 404 Not Found

**Cause**: Traefik cannot find the service or domain routing is incorrect

**Solutions**:
1. Verify the container is on the `traefik-net` network:
   ```bash
   docker inspect pipeshub-ai-pipeshub-ai-1 | grep traefik-net
   ```

2. Check Traefik labels are correctly applied:
   ```bash
   docker inspect pipeshub-ai-pipeshub-ai-1 | grep -A 20 Labels
   ```

3. Ensure DNS is pointing to the correct server

### Issue: SSL Certificate Not Working

**Cause**: Let's Encrypt cannot validate domain ownership

**Solutions**:
1. Verify DNS records are propagated:
   ```bash
   nslookup pipeshub.benjaminsanders.com
   ```

2. Check port 80 and 443 are accessible:
   ```bash
   sudo netstat -tlnp | grep -E ':(80|443)'
   ```

3. Review Traefik logs for certificate errors:
   ```bash
   docker logs traefik 2>&1 | grep -i acme
   ```

4. Ensure email is set in Traefik configuration

### Issue: Webhooks Not Working

**Cause**: Connector backend not accessible or environment variable not set

**Solutions**:
1. Verify `CONNECTOR_PUBLIC_BACKEND` is set in `.env`:
   ```bash
   grep CONNECTOR_PUBLIC_BACKEND .env
   ```

2. Test connector endpoint accessibility:
   ```bash
   curl https://pipeshub-connector.benjaminsanders.com/health
   ```

3. Check connector backend logs:
   ```bash
   docker compose -f docker-compose.prod.yml logs pipeshub-ai | grep -i connector
   ```

### Issue: Services Cannot Communicate Internally

**Cause**: Container networking issue

**Solutions**:
1. Verify containers are on both default and traefik-net networks:
   ```bash
   docker network inspect traefik-net
   docker network inspect pipeshub-ai_default
   ```

2. Check all dependent services are running:
   ```bash
   docker compose -f docker-compose.prod.yml ps
   ```

### Issue: Port Conflicts

**Cause**: Another service is using the same ports

**Solutions**:
1. Check what's using the ports:
   ```bash
   sudo netstat -tlnp | grep -E ':(3000|8088)'
   ```

2. Either stop conflicting services or change port mappings in docker-compose

## Security Best Practices

1. **Strong Passwords**: Use strong, unique passwords for all database services
   ```bash
   # Generate secure passwords
   openssl rand -base64 32
   ```

2. **Firewall Configuration**: Only expose necessary ports
   ```bash
   # Allow only 80 and 443
   sudo ufw allow 80/tcp
   sudo ufw allow 443/tcp
   sudo ufw enable
   ```

3. **Regular Updates**: Keep Traefik and PipesHub updated
   ```bash
   # Update images
   docker compose -f docker-compose.prod.yml pull
   docker compose -f docker-compose.prod.yml up -d
   ```

4. **SSL/TLS Only**: Ensure HTTP is redirected to HTTPS (configured in Traefik)

5. **Access Control**: Use Traefik middleware for additional authentication if needed
   ```yaml
   # Example: Add BasicAuth middleware
   labels:
     - "traefik.http.routers.pipeshub-frontend.middlewares=auth"
     - "traefik.http.middlewares.auth.basicauth.users=user:$$apr1$$..."
   ```

6. **Monitor Logs**: Regularly review logs for suspicious activity
   ```bash
   docker compose -f docker-compose.prod.yml logs --tail=100 -f
   ```

## Advanced Configuration

### Custom Middleware

Add rate limiting or custom headers:

```yaml
labels:
  # Rate limiting
  - "traefik.http.middlewares.ratelimit.ratelimit.average=100"
  - "traefik.http.middlewares.ratelimit.ratelimit.burst=50"
  - "traefik.http.routers.pipeshub-frontend.middlewares=ratelimit"

  # Security headers
  - "traefik.http.middlewares.security-headers.headers.stsSeconds=31536000"
  - "traefik.http.middlewares.security-headers.headers.stsIncludeSubdomains=true"
  - "traefik.http.routers.pipeshub-frontend.middlewares=security-headers"
```

### Multiple Domains

Route multiple domains to the same service:

```yaml
labels:
  - "traefik.http.routers.pipeshub-frontend.rule=Host(`pipeshub.benjaminsanders.com`) || Host(`pipeshub.example.com`)"
```

### Path-Based Routing

Route based on URL paths:

```yaml
labels:
  - "traefik.http.routers.pipeshub-api.rule=Host(`pipeshub.benjaminsanders.com`) && PathPrefix(`/api`)"
```

## Support

For issues or questions:
- PipesHub Documentation: https://docs.pipeshub.com/
- GitHub Issues: https://github.com/pipeshub-ai/pipeshub-ai/issues
- Discord Community: https://discord.com/invite/K5RskzJBm2

## References

- [Traefik Documentation](https://doc.traefik.io/traefik/)
- [Let's Encrypt Documentation](https://letsencrypt.org/docs/)
- [Docker Compose Networking](https://docs.docker.com/compose/networking/)
- [PipesHub Official Docs](https://docs.pipeshub.com/)
