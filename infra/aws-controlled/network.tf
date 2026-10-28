# One VPC: a public subnet for the host (the only thing with a public address) and two private subnets for RDS (RDS
# requires two availability zones). The private subnets have no route to the internet: no NAT gateway, no internet
# gateway route. S3 traffic from the host goes through a gateway endpoint.

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true # RDS endpoint names resolve inside the VPC

  tags = { Name = var.environment }
}

# No rules: nothing may use the default security group.
resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.main.id
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = var.environment }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, 1)
  availability_zone       = var.availability_zones[0]
  map_public_ip_on_launch = false # the host gets its address from the Elastic IP only

  tags = { Name = "${var.environment}-public" }
}

resource "aws_subnet" "database" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, 11 + count.index)
  availability_zone = var.availability_zones[count.index]

  tags = { Name = "${var.environment}-database-${count.index}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${var.environment}-public" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# Database subnets: local VPC routing only.
resource "aws_route_table" "database" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.environment}-database" }
}

resource "aws_route_table_association" "database" {
  count          = 2
  subnet_id      = aws_subnet.database[count.index].id
  route_table_id = aws_route_table.database.id
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.public.id]

  tags = { Name = "${var.environment}-s3" }
}

# --- security groups ---------------------------------------------------------------------------------------------------

resource "aws_security_group" "host" {
  name        = "${var.environment}-host"
  description = "BAEC host: nginx on 80/443 only; no SSH (Session Manager)"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${var.environment}-host" }
}

resource "aws_vpc_security_group_ingress_rule" "host_https" {
  for_each          = toset(var.https_ingress_cidrs)
  security_group_id = aws_security_group.host.id
  description       = "HTTPS to the nginx proxy"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = each.value
}

resource "aws_vpc_security_group_ingress_rule" "host_http" {
  for_each          = toset(var.http_ingress_cidrs)
  security_group_id = aws_security_group.host.id
  description       = "HTTP: 308 redirect and ACME HTTP-01 only"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = each.value
}

resource "aws_vpc_security_group_egress_rule" "host_https_out" {
  security_group_id = aws_security_group.host.id
  description       = "HTTPS out: OIDC provider, ACME, Session Manager, S3, Secrets Manager, package mirrors"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "host_http_out" {
  security_group_id = aws_security_group.host.id
  description       = "HTTP out: Ubuntu package mirrors (packages are signature-verified by apt)"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "host_postgres_out" {
  security_group_id            = aws_security_group.host.id
  description                  = "PostgreSQL to RDS only"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.database.id
}

resource "aws_security_group" "database" {
  name        = "${var.environment}-database"
  description = "BAEC RDS: PostgreSQL from the BAEC host security group only"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${var.environment}-database" }
}

resource "aws_vpc_security_group_ingress_rule" "database_from_host" {
  security_group_id            = aws_security_group.database.id
  description                  = "PostgreSQL from the BAEC host only"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.host.id
}
# The database security group has no egress rule: RDS needs none for client connections.
