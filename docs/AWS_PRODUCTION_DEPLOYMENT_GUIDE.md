# AWS Production Deployment Guide (1 Million Users Scale)

This document provides a production-grade blueprint for deploying the **Enterprise IT Support Agentic RAG Copilot** on **Amazon Web Services (AWS)** for **1 Million registered users** (~100,000 DAU, 5,000–10,000 concurrent peak users, 100–300 Requests Per Second).

---

## 1. Architecture Topology

```text
                                [ 1 Million Users ]
                                         │
                                         ▼
                            ┌────────────────────────┐
                            │ Route 53 (DNS & SSL)   │
                            └────────────┬───────────┘
                                         │
                                         ▼
                            ┌────────────────────────┐
                            │ AWS WAF (DDoS Shield)  │
                            └────────────┬───────────┘
                                         │
                                         ▼
                            ┌────────────────────────┐
                            │ Application Load       │
                            │ Balancer (ALB)         │
                            └────────────┬───────────┘
                                         │
                 ┌───────────────────────┴───────────────────────┐
                 ▼                                               ▼
    ┌─────────────────────────┐                     ┌─────────────────────────┐
    │ EC2 Instance 1 (Target) │                     │ EC2 Instance N (ASG)    │
    │ Gunicorn + Uvicorn      │                     │ Auto-scales based on    │
    │ (Hardened Docker)       │                     │ CPU / Request Count     │
    └────────────┬────────────┘                     └────────────┬────────────┘
                 │                                               │
                 └───────────────────────┬───────────────────────┘
                                         │
         ┌───────────────────────────────┼───────────────────────────────┐
         ▼                               ▼                               ▼
┌─────────────────┐             ┌─────────────────┐             ┌─────────────────┐
│ AWS ElastiCache │             │ AWS RDS Postgres│             │ Pinecone Vector │
│ Redis Cluster   │             │ + RDS Proxy     │             │ Serverless DB   │
│ (Cache & Token  │             │ (Multi-AZ with  │             │ (Auto-scales to │
│ Rate Limiting)  │             │ Read Replicas)  │             │ millions of Qs) │
└─────────────────┘             └─────────────────┘             └─────────────────┘
         │                               │
         ▼                               ▼
┌─────────────────┐             ┌─────────────────┐
│ AWS S3 Bucket   │             │ AWS Secrets     │
│ (Uploaded Docs) │             │ Manager (Keys)  │
└─────────────────┘             └─────────────────┘
```

---

## 2. Capacity Sizing & Cost Estimation

| Layer | AWS Service | Instance / Specification | Quantity / Target | Est. Monthly Cost |
|---|---|---|---|---|
| **Compute** | EC2 Instances (ASG) | `c6i.xlarge` (4 vCPU, 8GB RAM, High Network) | Min: 3, Max: 15 (Avg: 5) | ~$650 - $950 |
| **Load Balancing**| AWS ALB | Application Load Balancer + LCUs | 1 ALB (Multi-AZ) | ~$45 - $80 |
| **Relational DB** | AWS RDS PostgreSQL | `db.r6g.xlarge` (4 vCPU, 32GB RAM, Multi-AZ) | 1 Primary + 1 Standby | ~$380 - $450 |
| **DB Pooling** | AWS RDS Proxy | Managed PostgreSQL connection pooler | 1 Proxy Endpoint | ~$30 - $60 |
| **In-Memory Cache**| AWS ElastiCache | Redis `cache.r6g.large` (2 vCPU, 13GB RAM) | 2 Nodes (Cluster Mode) | ~$140 - $180 |
| **Vector DB** | Pinecone | Serverless Index (`fde-it-support-rag`) | Pay-per-query units | ~$120 - $250 |
| **Object Storage**| AWS S3 | Standard Storage (`novaretail-it-copilot-docs`) | S3 Standard + Lifecycle | ~$10 - $25 |
| **AI Inference** | Google Gemini / OpenAI | Gemini 3.8 Flash + GPT-4o-mini fallback | 70% cached via Redis | ~$700 - $1,400 |
| **Monitoring** | CloudWatch & LangSmith | Logs, Metrics, Distributed Tracing | Retention: 30 days | ~$60 - $100 |
| **Total** | | | | **~$2,135 - $3,495 / mo** |

---

## 3. Step-by-Step AWS Setup Guide

### Step 1: Create VPC & Security Groups

Create a dedicated VPC with CIDR `10.0.0.0/16`:
- **Public Subnets**: `10.0.1.0/24`, `10.0.2.0/24` (For ALB)
- **Private App Subnets**: `10.0.10.0/24`, `10.0.20.0/24` (For EC2 instances)
- **Private Data Subnets**: `10.0.30.0/24`, `10.0.40.0/24` (For RDS & ElastiCache)

#### Security Group Rules:
1. **`sg-alb` (Load Balancer)**:
   - Inbound: `80` (HTTP) from `0.0.0.0/0`, `443` (HTTPS) from `0.0.0.0/0`.
   - Outbound: All traffic to `sg-ec2`.
2. **`sg-ec2` (Application Instances)**:
   - Inbound: `8080` from `sg-alb` only.
   - Outbound: `5432` to `sg-rds`, `6379` to `sg-redis`, `443` to Internet (via NAT Gateway).
3. **`sg-rds` (Database)**:
   - Inbound: `5432` from `sg-ec2` and RDS Proxy.
4. **`sg-redis` (Cache)**:
   - Inbound: `6379` from `sg-ec2`.

---

### Step 2: Provision RDS PostgreSQL with RDS Proxy

1. Create a PostgreSQL 16 database instance:
   ```bash
   aws rds create-db-instance \
     --db-instance-identifier it-copilot-postgres-prod \
     --db-instance-class db.r6g.xlarge \
     --engine postgres \
     --engine-version 16.3 \
     --master-username postgres \
     --master-user-password "YourSecureMasterPassword123!" \
     --allocated-storage 100 \
     --max-allocated-storage 500 \
     --multi-az \
     --vpc-security-group-ids "sg-rds-id" \
     --no-publicly-accessible
   ```
2. Enable **AWS RDS Proxy**:
   - Connection Pooling prevents client exhaustion when 20 EC2 instances scale up with Gunicorn workers.
   - Set `MaxConnectionsPercent` to `80`.

---

### Step 3: Provision ElastiCache for Redis Cluster

```bash
aws elasticache create-replication-group \
  --replication-group-id it-copilot-redis-prod \
  --replication-group-description "Production Redis Cache for Copilot" \
  --engine redis \
  --cache-node-type cache.r6g.large \
  --num-node-groups 1 \
  --replicas-per-node-group 1 \
  --automatic-failover-enabled \
  --security-group-ids "sg-redis-id"
```

---

### Step 4: S3 Bucket for Multi-Instance Document Ingestion

```bash
aws s3api create-bucket \
  --bucket novaretail-it-copilot-docs \
  --region us-east-1

# Enable Server-Side Encryption
aws s3api put-bucket-encryption \
  --bucket novaretail-it-copilot-docs \
  --server-side-encryption-configuration '{
    "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
  }'
```

---

### Step 5: Store Secrets in AWS Secrets Manager

Create the secret `prod/it-copilot/env`:

```bash
aws secretsmanager create-secret \
  --name "prod/it-copilot/env" \
  --description "Production environment variables for IT Support Copilot" \
  --secret-string file://.env.production
```

Sample `.env.production`:
```ini
APP_ENV=production
LITELLM_ENABLED=true
LITELLM_MODEL=gemini/gemini-3.8-flash
LITELLM_FALLBACK_MODEL=gpt-4o-mini
GEMINI_API_KEY=AIzaSy...
OPENAI_API_KEY=sk-proj-...
TAVILY_API_KEY=tvly-...
PINECONE_API_KEY=pcsk_...
PINECONE_INDEX_NAME=fde-it-support-rag
PINECONE_NAMESPACE=company-it-kb
JWT_SECRET_KEY=32-character-crypto-secure-salt-here
POSTGRES_ENABLED=true
DATABASE_URL=postgresql+psycopg2://postgres:password@it-copilot-proxy.proxy-xxxx.us-east-1.rds.amazonaws.com:5432/it_support_copilot
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=30
REDIS_ENABLED=true
REDIS_URL=redis://it-copilot-redis-prod.xxxx.ng.0001.use1.cache.amazonaws.com:6379/0
REDIS_CACHE_TTL=3600
S3_ENABLED=true
S3_BUCKET_NAME=novaretail-it-copilot-docs
AWS_REGION=us-east-1
WEB_CONCURRENCY=8
GUNICORN_TIMEOUT=120
```

---

### Step 6: Create EC2 Launch Template

1. **AMI**: Ubuntu 22.04 LTS or Ubuntu 24.04 LTS (x86_64).
2. **Instance Type**: `c6i.xlarge` (4 vCPU, 8GB RAM).
3. **IAM Instance Profile**: Attach `EC2-Copilot-Role` with policies:
   - `AmazonSSMManagedInstanceCore`
   - `SecretsManagerReadWrite` (scoped to `prod/it-copilot/*`)
   - `AmazonS3FullAccess` (scoped to `novaretail-it-copilot-docs`)
4. **User Data**: Paste the contents of [`deploy/aws_ec2_user_data.sh`](file:///d:/New%20folder%20%284%29/Enterprise-IT-Support-Agentic-RAG-Copilot/deploy/aws_ec2_user_data.sh).

---

### Step 7: Configure Application Load Balancer (ALB)

1. Create Target Group:
   - Target Type: `Instances`
   - Protocol: `HTTP`, Port: `8080`
   - Healthcheck Path: `/api/health`
   - Healthy threshold: `2`, Unhealthy threshold: `3`
   - Timeout: `5s`, Interval: `15s`
2. Configure ALB:
   - Listener `80` (HTTP): Redirect to `443` (HTTPS).
   - Listener `443` (HTTPS): Attach ACM Certificate, forward to Target Group.

---

### Step 8: Auto Scaling Group (ASG) Configuration

1. Connect to Launch Template.
2. Capacity:
   - Minimum: `3`
   - Desired: `3`
   - Maximum: `15`
3. Dynamic Scaling Policies:
   - **Policy 1**: ALB Request Count per Target = `1000` requests/target/minute.
   - **Policy 2**: Average CPU Utilization = `70%`.

---

## 4. Production Verification Commands

Once the EC2 instances are registered to the ALB:

### Verify Public ALB Endpoint
```bash
curl -I https://copilot.yourdomain.com/api/health
```
*Expected response:*
```http
HTTP/2 200 
content-type: application/json
```

### Inspect Detailed Health Status
```bash
curl -s https://copilot.yourdomain.com/api/health | jq .
```
*Expected payload:*
```json
{
  "status": "ok",
  "service": "Enterprise IT Support Agentic RAG Copilot",
  "database": "postgresql",
  "postgres_connected": true,
  "cache": "redis",
  "redis_connected": true,
  "observability": "langsmith",
  "memory": "langmem",
  "llm_gateway": "litellm",
  "model": "gemini/gemini-3.8-flash",
  "guardrails": true,
  "ragas": true
}
```

---

## 5. CloudWatch Monitoring & Alerts

Set up the following CloudWatch Alarms for production operations:

1. **High 5XX Error Rate**:
   - Metric: `HTTPCode_Target_5XX_Count` > 10 for 2 consecutive periods of 1 minute.
   - Action: Send SNS notification to `#it-ops-pagerduty`.
2. **Target Response Time**:
   - Metric: `TargetResponseTime` > 4.5 seconds for 3 periods.
3. **Database CPU Utilization**:
   - Metric: `CPUUtilization` on RDS > 80% for 5 minutes.
4. **Redis Cache Evictions**:
   - Metric: `Evictions` > 1000 (Indicates Redis memory needs expansion).

---

## 6. Zero-Downtime Deployment via GitHub Actions

Whenever you merge to `main`, [`.github/workflows/deploy.yml`](file:///d:/New%20folder%20%284%29/Enterprise-IT-Support-Agentic-RAG-Copilot/.github/workflows/deploy.yml) automatically:
1. Executes unit tests (`pytest tests/`).
2. Builds and tags production container image in Amazon ECR.
3. Triggers an **AWS Auto Scaling Group Instance Refresh** (`start-instance-refresh`).
4. Performs a rolling update replacing instances 1-by-1 with zero downtime!
