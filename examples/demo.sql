-- python main.py --db :memory: examples/demo.sql
CREATE TABLE users (id INT PRIMARY KEY, name TEXT, age INT, city TEXT);

INSERT INTO users VALUES
  (1, 'Asha',  25, 'Lucknow'),
  (2, 'Ravi',  31, 'Delhi'),
  (3, 'Meera', 25, 'Mumbai'),
  (4, 'Kabir', 40, 'Lucknow');

SELECT * FROM users;
SELECT name, age + 1 FROM users WHERE city = 'Lucknow' ORDER BY age DESC;
SELECT COUNT(*), AVG(age), MAX(age) FROM users;
UPDATE users SET age = age + 1 WHERE name LIKE 'A%';
DELETE FROM users WHERE age > 35;

-- the optimizer at work: primary-key lookup uses the index, other columns scan
EXPLAIN SELECT * FROM users WHERE id = 2;
EXPLAIN SELECT * FROM users WHERE city = 'Delhi';
CREATE INDEX ON users (city);
EXPLAIN SELECT * FROM users WHERE city = 'Delhi' AND age > 20;
