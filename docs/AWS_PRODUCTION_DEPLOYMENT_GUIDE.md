# AWS Production Deployment Guide (1 Million Users Scale)

This guide provides the complete, production-grade infrastructure deployment manual for the **Enterprise IT Support Agentic RAG Copilot** on **Amazon Web Services (AWS)** using the **AWS CLI**. 

It is sized to comfortably handle **1 Million registered users** (~100,000 DAU, 5,000–10,000 concurrent peak users, 100–300 sustained Requests Per Second).

---

## Table of Contents

1. [Architecture Overview & AWS Service Matrix](#1-architecture-overview--aws-service-matrix)
2. [Capacity Sizing & Monthly Cost Estimation](#2-capacity-sizing--monthly-cost-estimation)
3. [Prerequisites & AWS CLI Configuration](#3-prerequisites--aws-cli-configuration)
4. [Step 1: Amazon VPC & High-Availability Networking](#step-1-amazon-vpc--high-availability-networking)
5. [Step 2: AWS Security Groups (Least-Privilege Isolation)](#step-2-aws-security-groups-least-privilege-isolation)
6. [Step 3: AWS IAM Roles & Instance Profiles](#step-3-aws-iam-roles--instance-profiles)
7. [Step 4: Amazon S3 (Centralized Document Storage)](#step-4-amazon-s3-centralized-document-storage)
8. [Step 5: AWS Secrets Manager (Configuration Injection)](#step-5-aws-secrets-manager-configuration-injection)
9. [Step 6: Amazon ECR (Production Container Registry)](#step-6-amazon-ecr-production-container-registry)
10. [Step 7: Amazon RDS PostgreSQL 16 (Multi-AZ) & RDS Proxy](#step-7-amazon-rds-postgresql-16-multi-az--rds-proxy)
11. [Step 8: Amazon ElastiCache for Redis 7 (Cluster Mode)](#step-8-amazon-elasticache-for-redis-7-cluster-mode)
12. [Step 9: Amazon EC2 Launch Template & User-Data Bootstrap](#step-9-amazon-ec2-launch-template--user-data-bootstrap)
13. [Step 10: Elastic Load Balancing (Application Load Balancer - ALB)](#step-10-elastic-load-balancing-application-load-balancer---alb)
14. [Step 11: Amazon EC2 Auto Scaling Group (ASG) & Dynamic Policies](#step-11-amazon-ec2-auto-scaling-group-asg--dynamic-policies)
15. [Step 12: AWS WAF (Web Application Firewall) & Rate Limiting](#step-12-aws-waf-web-application-firewall--rate-limiting)
16. [Step 13: AWS Certificate Manager (ACM) & Amazon Route 53 (DNS)](#step-13-aws-certificate-manager-acm--amazon-route-53-dns)
17. [Step 14: Amazon CloudWatch Monitoring, Alarms & SNS Alerting](#step-14-amazon-cloudwatch-monitoring-alarms--sns-alerting)
18. [Step 15: Zero-Downtime Rolling Update via AWS CLI](#step-15-zero-downtime-rolling-update-via-aws-cli)

---

## 1. Architecture Overview & AWS Service Matrix

```text
                                [ 1 Million Enterprise Users ]
                                               │
                                               ▼
                                  ┌────────────────────────┐
                                  │ Route 53 (DNS & Alias) │
                                  └────────────┬───────────┘
                                               │
                                               ▼
                                  ┌────────────────────────┐
                                  │ AWS WAF (DDoS & Rate)  │
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
      │ (Query Cache &  │             │ (Multi-AZ with  │             │ (External Dense │
      │ Token Buckets)  │             │ Connection Pool)│             │ Semantic Index) │
      └─────────────────┘             └─────────────────┘             └─────────────────┘
               │                               │
               ▼                               ▼
      ┌─────────────────┐             ┌─────────────────┐
      │ AWS S3 Bucket   │             │ AWS Secrets     │
      │ (Uploaded Docs) │             │ Manager (Keys)  │
      └─────────────────┘             └─────────────────┘
```

### Complete AWS Services Used:

| # | AWS Service | Purpose in Architecture | Why It Is Critical for 1M Scale |
|---|---|---|---|
| 1 | **Amazon VPC & Subnets** | Isolated network topology with Multi-AZ redundancy | Isolates database and cache into private non-routable subnets. |
| 2 | **AWS Security Groups** | Stateful firewall rules at the network interface layer | Zero-Trust isolation (only ALB can talk to EC2, only EC2 to DB/Cache). |
| 3 | **AWS IAM** | Role-based permissions for EC2 instances | Zero hardcoded AWS credentials on EC2 disks. |
| 4 | **Amazon S3** | Durable object storage for uploaded knowledge docs | Stateless EC2 nodes can scale to 15+ instances without localized disk sync. |
| 5 | **AWS Secrets Manager** | Encrypted key-value store for `.env` credentials | Injects production API keys at instance boot time. |
| 6 | **Amazon ECR** | Private, highly scalable Docker container registry | Fast container image pulls for new Auto Scaling instances. |
| 7 | **Amazon RDS PostgreSQL** | Relational ACID database (Users, Audits, Tickets, Evals) | Multi-AZ automated failover with 99.95% uptime SLA. |
| 8 | **AWS RDS Proxy** | Managed PostgreSQL connection pooler | Prevents database connection pool exhaustion when 15 EC2 instances run 8 workers each (120+ connections). |
| 9 | **Amazon ElastiCache** | Multi-AZ Redis 7 In-Memory caching cluster | Reduces database & LLM token load by up to 70% with sub-millisecond responses. |
| 10 | **Amazon EC2 & ASG** | Compute cluster running multi-worker Gunicorn | Horizontally scales compute from 3 up to 15 instances based on traffic. |
| 11 | **AWS Application Load Balancer** | Layer-7 load balancing with SSL termination | Distributes incoming HTTPS traffic across EC2 targets and performs healthchecks. |
| 12 | **AWS WAF** | Web Application Firewall protecting the ALB | Blocks DDoS, SQL injection, and enforces IP rate limits (500 req/5min). |
| 13 | **AWS Certificate Manager** | Managed SSL/TLS certificates | Free, auto-renewing TLS certificates for HTTPS encryption in transit. |
| 14 | **Amazon Route 53** | Low-latency DNS routing | Ultra-fast Anycast DNS resolution with health-check failover. |
| 15 | **Amazon CloudWatch & SNS**| Metrics, log aggregation, and real-time alerts | PagerDuty / email alerts when latency > 4s, 5xx errors > 1%, or CPU > 75%. |

---

## 2. Capacity Sizing & Monthly Cost Estimation

| Component | AWS Resource | Sizing Specification | Monthly Cost (Est.) |
|---|---|---|---|
| **Compute** | EC2 Auto Scaling Group | 3 to 15 × `c6i.xlarge` (Avg: 5 instances) | ~$650 – $950 |
| **Load Balancer** | AWS ALB | Multi-AZ ALB + LCU bandwidth | ~$45 – $80 |
| **Relational Database**| AWS RDS PostgreSQL | `db.r6g.xlarge` Multi-AZ (4 vCPU, 32GB RAM, 200GB GP3) | ~$380 – $450 |
| **Connection Pooler** | AWS RDS Proxy | Managed RDS Proxy | ~$30 – $60 |
| **In-Memory Cache** | AWS ElastiCache | Redis `cache.r6g.large` Multi-AZ (2 Nodes) | ~$140 – $180 |
| **Document Storage** | Amazon S3 | Standard S3 Storage + Lifecycle rules | ~$10 – $25 |
| **Container Registry** | Amazon ECR | Private Docker Image Repository | ~$5 – $10 |
| **Security & Secrets** | AWS WAF & Secrets Manager| WAF Managed Rules + Secret Rotation | ~$35 – $50 |
| **AI Inference** | Google Gemini / OpenAI | Gemini 3.8 Flash + GPT-4o-mini fallback (70% cached) | ~$700 – $1,400 |
| **Vector DB** | Pinecone Serverless | Serverless Query Units | ~$120 – $250 |
| **Observability** | CloudWatch & LangSmith | Logs, Metrics, Tracing | ~$60 – $100 |
| **Total Approximate** | | **Complete Production Stack** | **~$2,175 – $3,555 / mo** |

---

## 3. Prerequisites & AWS CLI Configuration

Ensure you have the latest AWS CLI v2 installed and configured:

```bash
# Verify AWS CLI installation
aws --version

# Set default region and output format
export AWS_REGION="us-east-1"
export AWS_DEFAULT_OUTPUT="json"

# Authenticate with AWS Administrator credentials
aws configure
```

---

## Step 1: Amazon VPC & High-Availability Networking

Deploy an enterprise VPC across 2 Availability Zones (`us-east-1a` and `us-east-1b`):

### 1.1 Create the VPC
```bash
VPC_ID=$(aws ec2 create-vpc \
  --cidr-block 10.0.0.0/16 \
  --tag-specifications 'ResourceType=vpc,Tags=[{Key=Name,Value=it-copilot-vpc}]' \
  --query 'Vpc.VpcId' --output text)

echo "Created VPC: $VPC_ID"

# Enable DNS support and hostnames
aws ec2 modify-vpc-attribute --vpc-id "$VPC_ID" --enable-dns-support '{"Value": true}'
aws ec2 modify-vpc-attribute --vpc-id "$VPC_ID" --enable-dns-hostnames '{"Value": true}'
```

### 1.2 Create Internet Gateway & Attach to VPC
```bash
IGW_ID=$(aws ec2 create-internet-gateway \
  --tag-specifications 'ResourceType=internet-gateway,Tags=[{Key=Name,Value=it-copilot-igw}]' \
  --query 'InternetGateway.InternetGatewayId' --output text)

aws ec2 attach-internet-gateway --vpc-id "$VPC_ID" --internet-gateway-id "$IGW_ID"
echo "Attached IGW: $IGW_ID"
```

### 1.3 Create Subnets (Public, Private App, Private Data)
```bash
# Public Subnets (For ALB)
PUB_SUB1=$(aws ec2 create-subnet --vpc-id "$VPC_ID" --cidr-block 10.0.1.0/24 --availability-zone us-east-1a \
  --tag-specifications 'ResourceType=subnet,Tags=[{Key=Name,Value=it-copilot-pub-1a}]' --query 'Subnet.SubnetId' --output text)

PUB_SUB2=$(aws ec2 create-subnet --vpc-id "$VPC_ID" --cidr-block 10.0.2.0/24 --availability-zone us-east-1b \
  --tag-specifications 'ResourceType=subnet,Tags=[{Key=Name,Value=it-copilot-pub-1b}]' --query 'Subnet.SubnetId' --output text)

# Private App Subnets (For EC2 Compute)
APP_SUB1=$(aws ec2 create-subnet --vpc-id "$VPC_ID" --cidr-block 10.0.10.0/24 --availability-zone us-east-1a \
  --tag-specifications 'ResourceType=subnet,Tags=[{Key=Name,Value=it-copilot-app-1a}]' --query 'Subnet.SubnetId' --output text)

APP_SUB2=$(aws ec2 create-subnet --vpc-id "$VPC_ID" --cidr-block 10.0.20.0/24 --availability-zone us-east-1b \
  --tag-specifications 'ResourceType=subnet,Tags=[{Key=Name,Value=it-copilot-app-1b}]' --query 'Subnet.SubnetId' --output text)

# Private Data Subnets (For RDS & ElastiCache)
DATA_SUB1=$(aws ec2 create-subnet --vpc-id "$VPC_ID" --cidr-block 10.0.30.0/24 --availability-zone us-east-1a \
  --tag-specifications 'ResourceType=subnet,Tags=[{Key=Name,Value=it-copilot-data-1a}]' --query 'Subnet.SubnetId' --output text)

DATA_SUB2=$(aws ec2 create-subnet --vpc-id "$VPC_ID" --cidr-block 10.0.40.0/24 --availability-zone us-east-1b \
  --tag-specifications 'ResourceType=subnet,Tags=[{Key=Name,Value=it-copilot-data-1b}]' --query 'Subnet.SubnetId' --output text)
```

### 1.4 Setup NAT Gateway (For Private Subnet Outbound Internet)
Private EC2 instances require outbound internet access to call Google Gemini, OpenAI, Tavily, and Pinecone:

```bash
# Allocate Elastic IP for NAT Gateway
EIP_ALLOC=$(aws ec2 allocate-address --domain vpc --query 'AllocationId' --output text)

# Create NAT Gateway in Public Subnet 1a
NAT_GW_ID=$(aws ec2 create-nat-gateway \
  --subnet-id "$PUB_SUB1" \
  --allocation-id "$EIP_ALLOC" \
  --tag-specifications 'ResourceType=natgateway,Tags=[{Key=Name,Value=it-copilot-nat}]' \
  --query 'NatGateway.NatGatewayId' --output text)

echo "Waiting for NAT Gateway to become available..."
aws ec2 wait nat-gateway-available --nat-gateway-ids "$NAT_GW_ID"
```

### 1.5 Configure Route Tables
```bash
# Public Route Table (Routes 0.0.0.0/0 to Internet Gateway)
PUB_RT=$(aws ec2 create-route-table --vpc-id "$VPC_ID" \
  --tag-specifications 'ResourceType=route-table,Tags=[{Key=Name,Value=it-copilot-pub-rt}]' --query 'RouteTable.RouteTableId' --output text)

aws ec2 create-route --route-table-id "$PUB_RT" --destination-cidr-block 0.0.0.0/0 --gateway-id "$IGW_ID"
aws ec2 associate-route-table --subnet-id "$PUB_SUB1" --route-table-id "$PUB_RT"
aws ec2 associate-route-table --subnet-id "$PUB_SUB2" --route-table-id "$PUB_RT"

# Private Route Table (Routes 0.0.0.0/0 to NAT Gateway)
PRIV_RT=$(aws ec2 create-route-table --vpc-id "$VPC_ID" \
  --tag-specifications 'ResourceType=route-table,Tags=[{Key=Name,Value=it-copilot-priv-rt}]' --query 'RouteTable.RouteTableId' --output text)

aws ec2 create-route --route-table-id "$PRIV_RT" --destination-cidr-block 0.0.0.0/0 --nat-gateway-id "$NAT_GW_ID"
aws ec2 associate-route-table --subnet-id "$APP_SUB1" --route-table-id "$PRIV_RT"
aws ec2 associate-route-table --subnet-id "$APP_SUB2" --route-table-id "$PRIV_RT"
```

---

## Step 2: AWS Security Groups (Least-Privilege Isolation)

Create 4 dedicated security groups enforcing strict zero-trust boundaries:

```bash
# 1. ALB Security Group (Public HTTPS/HTTP)
SG_ALB=$(aws ec2 create-security-group --group-name "sg-copilot-alb" --description "Security Group for ALB" --vpc-id "$VPC_ID" --query 'GroupId' --output text)
aws ec2 authorize-security-group-ingress --group-id "$SG_ALB" --protocol tcp --port 80 --cidr 0.0.0.0/0
aws ec2 authorize-security-group-ingress --group-id "$SG_ALB" --protocol tcp --port 443 --cidr 0.0.0.0/0

# 2. EC2 Compute Security Group (Port 8080 from ALB only)
SG_EC2=$(aws ec2 create-security-group --group-name "sg-copilot-ec2" --description "Security Group for EC2 Backend" --vpc-id "$VPC_ID" --query 'GroupId' --output text)
aws ec2 authorize-security-group-ingress --group-id "$SG_EC2" --protocol tcp --port 8080 --source-group "$SG_ALB"

# 3. RDS PostgreSQL Security Group (Port 5432 from EC2 only)
SG_RDS=$(aws ec2 create-security-group --group-name "sg-copilot-rds" --description "Security Group for RDS Postgres" --vpc-id "$VPC_ID" --query 'GroupId' --output text)
aws ec2 authorize-security-group-ingress --group-id "$SG_RDS" --protocol tcp --port 5432 --source-group "$SG_EC2"

# 4. ElastiCache Redis Security Group (Port 6379 from EC2 only)
SG_REDIS=$(aws ec2 create-security-group --group-name "sg-copilot-redis" --description "Security Group for Redis Cache" --vpc-id "$VPC_ID" --query 'GroupId' --output text)
aws ec2 authorize-security-group-ingress --group-id "$SG_REDIS" --protocol tcp --port 6379 --source-group "$SG_EC2"

echo "Security Groups Created: ALB=$SG_ALB, EC2=$SG_EC2, RDS=$SG_RDS, REDIS=$SG_REDIS"
```

---

## Step 3: AWS IAM Roles & Instance Profiles

EC2 instances must securely fetch credentials from AWS Secrets Manager and upload documents to S3 without embedded credentials:

```bash
# 1. Create Trust Policy
cat << 'EOF' > /tmp/ec2-trust-policy.json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "ec2.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

aws iam create-role --role-name "EC2-Copilot-Role" --assume-role-policy-document file:///tmp/ec2-trust-policy.json

# 2. Attach AWS Managed SSM Policy (for Session Manager shell without open SSH port 22)
aws iam attach-role-policy --role-name "EC2-Copilot-Role" --policy-arn "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"

# 3. Create Custom Policy for S3, Secrets Manager, and ECR access
cat << 'EOF' > /tmp/copilot-permissions-policy.json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret"
      ],
      "Resource": "arn:aws:secretsmanager:*:*:secret:prod/it-copilot/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::novaretail-it-copilot-docs",
        "arn:aws:s3:::novaretail-it-copilot-docs/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "ecr:GetAuthorizationToken",
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage"
      ],
      "Resource": "*"
    }
  ]
}
EOF

aws iam put-role-policy --role-name "EC2-Copilot-Role" --policy-name "CopilotAppPermissions" --policy-document file:///tmp/copilot-permissions-policy.json

# 4. Create Instance Profile & Attach to Role
aws iam create-instance-profile --instance-profile-name "EC2-Copilot-Profile"
aws iam add-role-to-instance-profile --instance-profile-name "EC2-Copilot-Profile" --role-name "EC2-Copilot-Role"
```

---

## Step 4: Amazon S3 (Centralized Document Storage)

Create an encrypted, private S3 bucket to centrally store uploaded documents across all autoscaling EC2 instances:

```bash
BUCKET_NAME="novaretail-it-copilot-docs"

# Create S3 Bucket
aws s3api create-bucket \
  --bucket "$BUCKET_NAME" \
  --region "$AWS_REGION"

# Enable Default KMS / AES256 Server-Side Encryption
aws s3api put-bucket-encryption \
  --bucket "$BUCKET_NAME" \
  --server-side-encryption-configuration '{
    "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
  }'

# Block all Public Access (Enterprise Security Compliance)
aws s3api put-public-access-block \
  --bucket "$BUCKET_NAME" \
  --public-access-block-configuration '{
    "BlockPublicAcls": true,
    "IgnorePublicAcls": true,
    "BlockPublicPolicy": true,
    "RestrictPublicBuckets": true
  }'
```

---

## Step 5: AWS Secrets Manager (Configuration Injection)

Store the complete `.env` secrets safely in AWS Secrets Manager:

```bash
# Create the production secret
aws secretsmanager create-secret \
  --name "prod/it-copilot/env" \
  --description "Production environment credentials for IT Support Copilot" \
  --secret-string '{
    "APP_ENV": "production",
    "LITELLM_ENABLED": "true",
    "LITELLM_MODEL": "gemini/gemini-3.8-flash",
    "LITELLM_FALLBACK_MODEL": "gpt-4o-mini",
    "GEMINI_API_KEY": "AIzaSy...",
    "OPENAI_API_KEY": "sk-proj-...",
    "TAVILY_API_KEY": "tvly-...",
    "PINECONE_API_KEY": "pcsk_...",
    "PINECONE_INDEX_NAME": "fde-it-support-rag",
    "PINECONE_NAMESPACE": "company-it-kb",
    "JWT_SECRET_KEY": "generate-a-cryptographically-secure-random-32-byte-key",
    "POSTGRES_ENABLED": "true",
    "DATABASE_URL": "postgresql+psycopg2://copilot_admin:YourDbPassword@it-copilot-proxy.proxy-xxxx.us-east-1.rds.amazonaws.com:5432/it_support_copilot",
    "DB_POOL_SIZE": "20",
    "DB_MAX_OVERFLOW": "30",
    "REDIS_ENABLED": "true",
    "REDIS_URL": "redis://it-copilot-redis-prod.xxxx.ng.0001.use1.cache.amazonaws.com:6379/0",
    "REDIS_CACHE_TTL": "3600",
    "S3_ENABLED": "true",
    "S3_BUCKET_NAME": "novaretail-it-copilot-docs",
    "AWS_REGION": "us-east-1",
    "WEB_CONCURRENCY": "8",
    "GUNICORN_TIMEOUT": "120"
  }'
```

---

## Step 6: Amazon ECR (Production Container Registry)

Create an Amazon Elastic Container Registry (ECR) repository to store immutable Docker image releases:

```bash
# Create ECR repository
aws ecr create-repository \
  --repository-name "it-support-copilot" \
  --image-scanning-configuration scanOnPush=true \
  --tag-mutability MUTABLE

# Set lifecycle policy (automatically purge untagged images older than 14 days)
cat << 'EOF' > /tmp/ecr-lifecycle.json
{
  "rules": [
    {
      "rulePriority": 1,
      "description": "Expire untagged images older than 14 days",
      "selection": { "tagStatus": "untagged", "countType": "sinceImagePushed", "countUnit": "days", "countNumber": 14 },
      "action": { "type": "expire" }
    }
  ]
}
EOF

aws ecr put-lifecycle-policy \
  --repository-name "it-support-copilot" \
  --lifecycle-policy-text file:///tmp/ecr-lifecycle.json
```

---

## Step 7: Amazon RDS PostgreSQL 16 (Multi-AZ) & RDS Proxy

### 7.1 Create DB Subnet Group
```bash
aws rds create-db-subnet-group \
  --db-subnet-group-name "copilot-db-subnets" \
  --db-subnet-group-description "Private Data Subnets for RDS" \
  --subnet-ids "$DATA_SUB1" "$DATA_SUB2"
```

### 7.2 Provision High-Availability Multi-AZ PostgreSQL 16
```bash
aws rds create-db-instance \
  --db-instance-identifier "it-copilot-postgres-prod" \
  --db-instance-class "db.r6g.xlarge" \
  --engine "postgres" \
  --engine-version "16.3" \
  --master-username "copilot_admin" \
  --master-user-password "YourStrongPasswordHere2026!" \
  --allocated-storage 100 \
  --max-allocated-storage 500 \
  --storage-type "gp3" \
  --multi-az \
  --db-subnet-group-name "copilot-db-subnets" \
  --vpc-security-group-ids "$SG_RDS" \
  --backup-retention-period 14 \
  --preferred-backup-window "03:00-04:00" \
  --no-publicly-accessible
```

### 7.3 Create AWS RDS Proxy (Connection Pooler)
15 EC2 instances running 8 workers each open 120+ concurrent connections. RDS Proxy pools and multiplexes connections to preserve database stability:

```bash
aws rds create-db-proxy \
  --db-proxy-name "it-copilot-proxy" \
  --engine-family "POSTGRESQL" \
  --auth '[{"AuthScheme":"SECRETS","SecretArn":"arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:prod/it-copilot/db","IAMAuth":"DISABLED"}]' \
  --role-arn "arn:aws:iam::ACCOUNT_ID:role/RDSProxyRole" \
  --vpc-subnet-ids "$DATA_SUB1" "$DATA_SUB2" \
  --vpc-security-group-ids "$SG_RDS"

# Register RDS DB Instance as Proxy Target
aws rds register-db-proxy-targets \
  --db-proxy-name "it-copilot-proxy" \
  --target-group-name "default" \
  --db-instance-identifiers "it-copilot-postgres-prod"
```

---

## Step 8: Amazon ElastiCache for Redis 7 (Cluster Mode)

### 8.1 Create Cache Subnet Group
```bash
aws elasticache create-cache-subnet-group \
  --cache-subnet-group-name "copilot-redis-subnets" \
  --cache-subnet-group-description "Subnet group for Redis Cache" \
  --subnet-ids "$DATA_SUB1" "$DATA_SUB2"
```

### 8.2 Create Multi-AZ Redis Replication Group
```bash
aws elasticache create-replication-group \
  --replication-group-id "it-copilot-redis-prod" \
  --replication-group-description "Production Multi-AZ Redis Cache for Copilot" \
  --engine "redis" \
  --engine-version "7.1" \
  --cache-node-type "cache.r6g.large" \
  --cache-subnet-group-name "copilot-redis-subnets" \
  --security-group-ids "$SG_REDIS" \
  --num-node-groups 1 \
  --replicas-per-node-group 1 \
  --automatic-failover-enabled \
  --at-rest-encryption-enabled \
  --transit-encryption-enabled
```

---

## Step 9: Amazon EC2 Launch Template & User-Data Bootstrap

Create an EC2 Launch Template that specifies the AMI, instance profile, security group, and base64-encoded bootstrap script:

```bash
# 1. Base64-encode the deployment user-data script
USER_DATA_B64=$(base64 -w 0 deploy/aws_ec2_user_data.sh)

# 2. Create the Launch Template JSON
cat << EOF > /tmp/launch-template-data.json
{
  "ImageId": "ami-04b70fa74e45c3917",
  "InstanceType": "c6i.xlarge",
  "IamInstanceProfile": { "Name": "EC2-Copilot-Profile" },
  "SecurityGroupIds": ["$SG_EC2"],
  "UserData": "$USER_DATA_B64",
  "BlockDeviceMappings": [
    {
      "DeviceName": "/dev/sda1",
      "Ebs": {
        "VolumeSize": 50,
        "VolumeType": "gp3",
        "Encrypted": true,
        "DeleteOnTermination": true
      }
    }
  ],
  "Monitoring": { "Enabled": true }
}
EOF

# 3. Create Launch Template in AWS
aws ec2 create-launch-template \
  --launch-template-name "it-copilot-launch-template" \
  --version-description "v1.0-production" \
  --launch-template-data file:///tmp/launch-template-data.json
```

---

## Step 10: Elastic Load Balancing (Application Load Balancer - ALB)

### 10.1 Create ALB Target Group
```bash
TG_ARN=$(aws elbv2 create-target-group \
  --name "copilot-alb-tg" \
  --protocol HTTP \
  --port 8080 \
  --vpc-id "$VPC_ID" \
  --target-type instance \
  --health-check-protocol HTTP \
  --health-check-port 8080 \
  --health-check-path "/api/health" \
  --health-check-interval-seconds 15 \
  --health-check-timeout-seconds 5 \
  --healthy-threshold-count 2 \
  --unhealthy-threshold-count 3 \
  --query 'TargetGroups[0].TargetGroupArn' --output text)

echo "Target Group ARN: $TG_ARN"
```

### 10.2 Create the Application Load Balancer
```bash
ALB_ARN=$(aws elbv2 create-load-balancer \
  --name "copilot-alb" \
  --subnets "$PUB_SUB1" "$PUB_SUB2" \
  --security-groups "$SG_ALB" \
  --scheme internet-facing \
  --type application \
  --ip-address-type ipv4 \
  --query 'LoadBalancers[0].LoadBalancerArn' --output text)

echo "ALB ARN: $ALB_ARN"
```

### 10.3 Create HTTP Port 80 Listener (Redirect to HTTPS)
```bash
aws elbv2 create-listener \
  --load-balancer-arn "$ALB_ARN" \
  --protocol HTTP \
  --port 80 \
  --default-actions Type=redirect,RedirectConfig='{Protocol=HTTPS,Port=443,StatusCode=HTTP_301}'
```

### 10.4 Create HTTPS Port 443 Listener (Forward to Target Group)
```bash
aws elbv2 create-listener \
  --load-balancer-arn "$ALB_ARN" \
  --protocol HTTPS \
  --port 443 \
  --certificates CertificateArn="arn:aws:acm:us-east-1:ACCOUNT_ID:certificate/YOUR-CERT-ID" \
  --default-actions Type=forward,TargetGroupArn="$TG_ARN"
```

---

## Step 11: Amazon EC2 Auto Scaling Group (ASG) & Dynamic Policies

### 11.1 Create the Auto Scaling Group
```bash
aws autoscaling create-auto-scaling-group \
  --auto-scaling-group-name "it-copilot-asg" \
  --launch-template LaunchTemplateName="it-copilot-launch-template",Version='$Latest' \
  --min-size 3 \
  --max-size 15 \
  --desired-capacity 3 \
  --vpc-zone-identifier "$APP_SUB1,$APP_SUB2" \
  --target-group-arns "$TG_ARN" \
  --health-check-type ELB \
  --health-check-grace-period 300
```

### 11.2 Attach Dynamic Scaling Policy 1: ALB Request Count
Scales instances up when requests exceed **1,000 requests/target/minute**:

```bash
cat << 'EOF' > /tmp/asg-policy-alb.json
{
  "TargetValue": 1000.0,
  "PredefinedMetricSpecification": {
    "PredefinedMetricType": "ALBRequestCountPerTarget",
    "ResourceLabel": "app/copilot-alb/XXXXXX/targetgroup/copilot-alb-tg/XXXXXX"
  }
}
EOF

aws autoscaling put-scaling-policy \
  --auto-scaling-group-name "it-copilot-asg" \
  --policy-name "alb-requests-scaling" \
  --policy-type TargetTrackingScaling \
  --target-tracking-configuration file:///tmp/asg-policy-alb.json
```

### 11.3 Attach Dynamic Scaling Policy 2: CPU Utilization
Scales instances up when average CPU exceeds **70%**:

```bash
cat << 'EOF' > /tmp/asg-policy-cpu.json
{
  "TargetValue": 70.0,
  "PredefinedMetricSpecification": {
    "PredefinedMetricType": "ASGAverageCPUUtilization"
  }
}
EOF

aws autoscaling put-scaling-policy \
  --auto-scaling-group-name "it-copilot-asg" \
  --policy-name "cpu-scaling" \
  --policy-type TargetTrackingScaling \
  --target-tracking-configuration file:///tmp/asg-policy-cpu.json
```

---

## Step 12: AWS WAF (Web Application Firewall) & Rate Limiting

Attach an AWS WAF Web ACL to protect the ALB against DDoS, brute force attacks, and abusive crawlers:

```bash
# Create WAF Web ACL with AWS Managed Common Rules & IP Rate Limiting (500 req / 5 min)
aws wafv2 create-web-acl \
  --name "copilot-waf-acl" \
  --scope REGIONAL \
  --default-action Allow={} \
  --rules '[
    {
      "Name": "AWSManagedRulesCommonRuleSet",
      "Priority": 1,
      "Statement": {
        "ManagedRuleGroupStatement": {
          "VendorName": "AWS",
          "Name": "AWSManagedRulesCommonRuleSet"
        }
      },
      "OverrideAction": { "None": {} },
      "VisibilityConfig": { "SampledRequestsEnabled": true, "CloudWatchMetricsEnabled": true, "MetricName": "AWSCommonRules" }
    },
    {
      "Name": "RateLimitRule",
      "Priority": 2,
      "Statement": {
        "RateBasedStatement": {
          "Limit": 500,
          "AggregateKeyType": "IP"
        }
      },
      "Action": { "Block": {} },
      "VisibilityConfig": { "SampledRequestsEnabled": true, "CloudWatchMetricsEnabled": true, "MetricName": "RateLimitMetric" }
    }
  ]' \
  --visibility-config SampledRequestsEnabled=true,CloudWatchMetricsEnabled=true,MetricName=CopilotWafMain

# Associate WAF ACL with Application Load Balancer
aws wafv2 associate-web-acl \
  --web-acl-arn "arn:aws:wafv2:us-east-1:ACCOUNT_ID:regional/webacl/copilot-waf-acl/XXXX" \
  --resource-arn "$ALB_ARN"
```

---

## Step 13: AWS Certificate Manager (ACM) & Amazon Route 53 (DNS)

### 13.1 Request Free Managed SSL Certificate
```bash
CERT_ARN=$(aws acm request-certificate \
  --domain-name "copilot.novaretail.com" \
  --validation-method DNS \
  --query 'CertificateArn' --output text)

echo "Requested ACM Certificate: $CERT_ARN"
```

### 13.2 Create Route 53 Alias Record Pointing to ALB
```bash
cat << EOF > /tmp/route53-record.json
{
  "Comment": "Alias record for IT Copilot ALB",
  "Changes": [
    {
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "copilot.novaretail.com",
        "Type": "A",
        "AliasTarget": {
          "HostedZoneId": "Z35SXDOTRQ7X7K",
          "DNSName": "copilot-alb-XXXXXX.us-east-1.elb.amazonaws.com",
          "EvaluateTargetHealth": true
        }
      }
    }
  ]
}
EOF

aws route53 change-resource-record-sets \
  --hosted-zone-id "Z_YOUR_HOSTED_ZONE_ID" \
  --change-batch file:///tmp/route53-record.json
```

---

## Step 14: Amazon CloudWatch Monitoring, Alarms & SNS Alerting

Set up real-time telemetry and automated incident response:

### 14.1 Create SNS Topic for DevOps Alerts
```bash
SNS_TOPIC_ARN=$(aws sns create-topic --name "it-copilot-devops-alerts" --query 'TopicArn' --output text)

# Subscribe on-call email
aws sns subscribe \
  --topic-arn "$SNS_TOPIC_ARN" \
  --protocol email \
  --notification-endpoint "devops-oncall@novaretail.com"
```

### 14.2 Alarm 1: ALB 5XX Error Spike
```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "Copilot-High-5XX-Errors" \
  --alarm-description "Triggers if ALB reports more than 10 5XX responses in 2 minutes" \
  --metric-name "HTTPCode_Target_5XX_Count" \
  --namespace "AWS/ApplicationELB" \
  --statistic Sum \
  --period 60 \
  --evaluation-periods 2 \
  --threshold 10 \
  --comparison-operator GreaterThanThreshold \
  --alarm-actions "$SNS_TOPIC_ARN"
```

### 14.3 Alarm 2: High Latency (> 4.0s)
```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "Copilot-High-Latency" \
  --alarm-description "Triggers if average response latency exceeds 4 seconds" \
  --metric-name "TargetResponseTime" \
  --namespace "AWS/ApplicationELB" \
  --statistic Average \
  --period 60 \
  --evaluation-periods 3 \
  --threshold 4.0 \
  --comparison-operator GreaterThanThreshold \
  --alarm-actions "$SNS_TOPIC_ARN"
```

### 14.4 Alarm 3: RDS High CPU (> 80%)
```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "Copilot-RDS-High-CPU" \
  --alarm-description "Triggers if RDS PostgreSQL CPU exceeds 80% for 5 minutes" \
  --metric-name "CPUUtilization" \
  --namespace "AWS/RDS" \
  --statistic Average \
  --period 300 \
  --evaluation-periods 1 \
  --threshold 80.0 \
  --comparison-operator GreaterThanThreshold \
  --dimensions Name=DBInstanceIdentifier,Value=it-copilot-postgres-prod \
  --alarm-actions "$SNS_TOPIC_ARN"
```

---

## Step 15: Zero-Downtime Rolling Update via AWS CLI

Whenever a new Docker image is pushed to Amazon ECR or code is merged into `main`, execute an instance refresh with a single CLI command:

```bash
# 1. Trigger Auto Scaling Group Rolling Refresh
aws autoscaling start-instance-refresh \
  --auto-scaling-group-name "it-copilot-asg" \
  --preferences '{"MinHealthyPercentage": 66, "InstanceWarmup": 60}'

# 2. Check Instance Refresh Progress in Real Time
aws autoscaling describe-instance-refreshes \
  --auto-scaling-group-name "it-copilot-asg" \
  --query 'InstanceRefreshes[0].{Status:Status,Percentage:PercentageComplete}'
```

---

## 4. Production Smoke-Test & Verification

Verify the deployed stack via curl:

```bash
# 1. Probe Public Health Endpoint
curl -s -I https://copilot.novaretail.com/api/health

# 2. Verify JSON Health Response
curl -s https://copilot.novaretail.com/api/health | jq .
```

*Expected JSON Output:*
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
