#!/usr/bin/env bash
# 容器创建时执行一次：安装系统依赖与 npm 依赖
set -e
cd "$(dirname "$0")/.."

echo "[setup] 安装 MySQL 8（Ubuntu 22.04 jammy 官方源）..."
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y mysql-server

echo "[setup] 安装 blockchain 依赖（ethers/solc/ganache）..."
cd blockchain && npm install && cd ..

echo "[setup] 安装 backend-js 依赖（express/cors/mysql2/ethers）..."
cd backend-js && npm install && cd ..

echo "[setup] 完成"
