-- 示例 SQL 文件：10条涵盖多种反模式与常见场景的 SQL
-- 1. SELECT * + 前缀通配符 LIKE
SELECT * FROM users WHERE name LIKE '%张%';

-- 2. JOIN + WHERE 多条件 AND + ORDER BY
SELECT * FROM users u JOIN orders o ON u.id = o.user_id
WHERE u.city = '北京' AND o.status = 'completed'
ORDER BY o.created_at DESC;

-- 3. LEFT JOIN + GROUP BY + 聚合 + LIMIT
SELECT u.id, u.name, COUNT(o.id) AS order_count
FROM users u LEFT JOIN orders o ON u.id = o.user_id
GROUP BY u.id, u.name
ORDER BY order_count DESC
LIMIT 10;

-- 4. SELECT * + 多列 AND 条件 + 范围查询
SELECT * FROM products
WHERE price > 1000 AND category = '电子' AND stock > 0;

-- 5. NOT IN 子查询（反模式）
SELECT * FROM users
WHERE id NOT IN (SELECT user_id FROM orders WHERE status = 'cancelled');

-- 6. WHERE 中对列使用函数（索引失效）
SELECT COUNT(*) FROM orders
WHERE DATE(created_at) = '2024-01-01';

-- 7. 笛卡尔积（缺少 JOIN 条件，严重反模式）
SELECT u.name, p.name
FROM users u, products p
WHERE u.id > 100;

-- 8. 多个 OR 条件
SELECT * FROM users
WHERE city = '上海' OR age = 25 OR status = 'active';

-- 9. UPDATE + WHERE
UPDATE users SET age = 26 WHERE name = '张伟';

-- 10. JOIN + GROUP BY + HAVING + 聚合
SELECT p.category, SUM(o.total_price) AS total
FROM orders o
JOIN products p ON o.product_id = p.id
GROUP BY p.category
HAVING total > 10000;
