# Terraform — Sixfold SRE Observability Platform Infrastructure
# Provisions: EKS cluster, monitoring namespace, CloudWatch alarms, PagerDuty integration

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.24"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.12"
    }
  }

  backend "s3" {
    bucket         = "sixfold-terraform-state"
    key            = "sre-observability/terraform.tfstate"
    region         = "eu-west-1"
    encrypt        = true
    dynamodb_table = "sixfold-terraform-locks"
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project     = "sixfold-sre"
      Environment = var.environment
      ManagedBy   = "terraform"
      Team        = "sre"
    }
  }
}

variable "aws_region" {
  description = "AWS region"
  default     = "eu-west-1"
}

variable "environment" {
  description = "Deployment environment"
  default     = "production"
}

variable "cluster_name" {
  description = "EKS cluster name"
  default     = "sixfold-production"
}

variable "pagerduty_p1_endpoint" {
  description = "PagerDuty P1 SNS endpoint URL"
  sensitive   = true
}

# EKS Cluster
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name    = var.cluster_name
  cluster_version = "1.29"

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  cluster_endpoint_public_access = false  # Private API only

  eks_managed_node_groups = {
    # General workloads
    general = {
      instance_types = ["m7g.xlarge"]
      min_size       = 3
      max_size       = 20
      desired_size   = 5

      labels = { role = "general" }
    }

    # LLM inference — GPU nodes
    gpu = {
      instance_types = ["g5.2xlarge"]
      ami_type       = "AL2_x86_64_GPU"
      min_size       = 1
      max_size       = 5
      desired_size   = 2

      labels = { role = "gpu", "nvidia.com/gpu" = "true" }
      taints = [{
        key    = "nvidia.com/gpu"
        value  = "true"
        effect = "NO_SCHEDULE"
      }]
    }
  }

  # Enable IRSA for service account role binding
  enable_irsa = true
}

# VPC
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = "sixfold-${var.environment}"
  cidr = "10.0.0.0/16"

  azs             = ["${var.aws_region}a", "${var.aws_region}b", "${var.aws_region}c"]
  private_subnets = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24", "10.0.103.0/24"]

  enable_nat_gateway   = true
  single_nat_gateway   = false  # HA: one per AZ
  enable_dns_hostnames = true
  enable_dns_support   = true

  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = 1
  }
}

# Prometheus long-term storage (Thanos)
resource "aws_s3_bucket" "thanos_metrics" {
  bucket = "sixfold-thanos-metrics-${var.environment}"

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "thanos_metrics" {
  bucket = aws_s3_bucket.thanos_metrics.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "thanos_metrics" {
  bucket = aws_s3_bucket.thanos_metrics.id

  rule {
    id     = "metrics-lifecycle"
    status = "Enabled"

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }
    transition {
      days          = 90
      storage_class = "GLACIER"
    }
    expiration {
      days = 365
    }
  }
}

# CloudWatch Alarms for critical services
resource "aws_cloudwatch_metric_alarm" "rds_high_cpu" {
  alarm_name          = "sixfold-rds-high-cpu-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = 60
  statistic           = "Average"
  threshold           = 80
  alarm_description   = "RDS CPU utilization exceeds 80% for 3 consecutive minutes"
  alarm_actions       = [aws_sns_topic.sre_alerts_p2.arn]
  ok_actions          = [aws_sns_topic.sre_alerts_p2.arn]
}

resource "aws_cloudwatch_metric_alarm" "eks_node_not_ready" {
  alarm_name          = "sixfold-eks-node-not-ready-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "cluster_failed_node_count"
  namespace           = "ContainerInsights"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  alarm_description   = "EKS node(s) are not ready"
  alarm_actions       = [aws_sns_topic.sre_alerts_p1.arn]

  dimensions = {
    ClusterName = var.cluster_name
  }
}

# SNS Topics for alert routing
resource "aws_sns_topic" "sre_alerts_p1" {
  name = "sixfold-sre-alerts-p1-${var.environment}"
}

resource "aws_sns_topic" "sre_alerts_p2" {
  name = "sixfold-sre-alerts-p2-${var.environment}"
}

# PagerDuty subscription for P1
resource "aws_sns_topic_subscription" "pagerduty_p1" {
  topic_arn = aws_sns_topic.sre_alerts_p1.arn
  protocol  = "https"
  endpoint  = var.pagerduty_p1_endpoint
}

# Monitoring namespace
resource "kubernetes_namespace" "monitoring" {
  metadata {
    name = "monitoring"
    labels = {
      "app.kubernetes.io/managed-by" = "terraform"
      "sixfold.ai/team"              = "sre"
    }
  }
}

# Outputs
output "cluster_name" {
  value = module.eks.cluster_name
}

output "thanos_bucket" {
  value = aws_s3_bucket.thanos_metrics.id
}

output "sre_p1_sns_arn" {
  value = aws_sns_topic.sre_alerts_p1.arn
}
