/**
 * MySQL 数据库访问模块（mysql2/promise 连接池）
 * =============================================
 * 职责：案件材料、实名哈希镜像、交易索引的读写。
 * 说明：账号信息不再入库；交易以链上为准，本库仅做镜像索引与业务查询。
 */
const mysql = require('mysql2/promise');
const { DB_CONFIG } = require('../config');

let pool = null;

/** 获取连接池（懒加载） */
function getPool() {
  if (!pool) {
    pool = mysql.createPool(DB_CONFIG);
  }
  return pool;
}

/**
 * 执行查询，返回行数组
 * @param {string} sql
 * @param {Array}  params
 */
async function query(sql, params = []) {
  const [rows] = await getPool().execute(sql, params);
  return rows;
}

/**
 * 执行单条查询，返回首行或 null
 */
async function queryOne(sql, params = []) {
  const rows = await query(sql, params);
  return rows[0] || null;
}

/**
 * 执行写操作，返回 ResultSetHeader（insertId / affectedRows）
 */
async function execute(sql, params = []) {
  const [result] = await getPool().execute(sql, params);
  return result;
}

/**
 * 测试数据库连接
 */
async function ping() {
  const conn = await getPool().getConnection();
  try {
    await conn.ping();
  } finally {
    conn.release();
  }
}

module.exports = { query, queryOne, execute, ping };
