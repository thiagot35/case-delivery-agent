-- Script para a criação do banco de dados do cardápio da Beauty Pizza em SQLite

-- Tabela para armazenar informações sobre as pizzas
CREATE TABLE IF NOT EXISTS pizzas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sabor TEXT NOT NULL,
    descricao TEXT NOT NULL,
    ingredientes TEXT NOT NULL
);

-- Tabela para os tamanhos de pizza disponíveis
CREATE TABLE IF NOT EXISTS tamanhos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tamanho TEXT NOT NULL UNIQUE
);

-- Tabela para os tipos de borda
CREATE TABLE IF NOT EXISTS bordas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo TEXT NOT NULL UNIQUE
);

-- Tabela de preços, relacionando pizza, tamanho e borda
CREATE TABLE IF NOT EXISTS precos (
    pizza_id INTEGER,
    tamanho_id INTEGER,
    borda_id INTEGER,
    preco REAL NOT NULL,
    PRIMARY KEY (pizza_id, tamanho_id, borda_id),
    FOREIGN KEY (pizza_id) REFERENCES pizzas(id),
    FOREIGN KEY (tamanho_id) REFERENCES tamanhos(id),
    FOREIGN KEY (borda_id) REFERENCES bordas(id)
);

-- Inserção de dados nas tabelas
INSERT INTO pizzas (sabor, descricao, ingredientes) VALUES
('Margherita', 'A clássica pizza italiana.', 'Molho de tomate, mussarela, manjericão fresco, azeite extra virgem.'),
('Pepperoni', 'A pizza mais pedida nos EUA.', 'Molho de tomate, mussarela, fatias de pepperoni.'),
('Quatro Queijos', 'Combinação de queijos para os amantes de laticínios.', 'Molho de tomate, mussarela, provolone, parmesão, gorgonzola.'),
('Calabresa', 'Saborosa pizza de calabresa com cebola.', 'Molho de tomate, mussarela, calabresa fatiada, cebola.'),
('Frango com Catupiry', 'Deliciosa pizza de frango desfiado com Catupiry original.', 'Molho de tomate, mussarela, frango desfiado, Catupiry.'),
('Doce de Leite com Coco', 'Uma opção doce para fechar a refeição.', 'Doce de leite cremoso, coco ralado, leite condensado.');


INSERT INTO tamanhos (tamanho) VALUES
('Pequena'),
('Média'),
('Grande');

INSERT INTO bordas (tipo) VALUES
('Tradicional'),
('Recheada com Cheddar'),
('Recheada com Catupiry');

-- Inserção de preços para cada combinação de pizza, tamanho e borda
-- Margherita
INSERT INTO precos (pizza_id, tamanho_id, borda_id, preco) VALUES
(1, 1, 1, 25.00), -- Pequena, Tradicional
(1, 2, 1, 35.00), -- Média, Tradicional
(1, 3, 1, 45.00), -- Grande, Tradicional
(1, 2, 2, 38.00), -- Média, Recheada com Cheddar
(1, 3, 2, 48.00), -- Grande, Recheada com Cheddar
(1, 2, 3, 39.00), -- Média, Recheada com Catupiry
(1, 3, 3, 49.00); -- Grande, Recheada com Catupiry

-- Pepperoni
INSERT INTO precos (pizza_id, tamanho_id, borda_id, preco) VALUES
(2, 1, 1, 28.00),
(2, 2, 1, 38.00),
(2, 3, 1, 48.00),
(2, 2, 2, 41.00),
(2, 3, 2, 51.00),
(2, 2, 3, 42.00),
(2, 3, 3, 52.00);

-- Quatro Queijos
INSERT INTO precos (pizza_id, tamanho_id, borda_id, preco) VALUES
(3, 1, 1, 30.00),
(3, 2, 1, 40.00),
(3, 3, 1, 50.00),
(3, 2, 2, 43.00),
(3, 3, 2, 53.00),
(3, 2, 3, 44.00),
(3, 3, 3, 54.00);

-- Calabresa
INSERT INTO precos (pizza_id, tamanho_id, borda_id, preco) VALUES
(4, 1, 1, 27.00),
(4, 2, 1, 37.00),
(4, 3, 1, 47.00),
(4, 2, 2, 40.00),
(4, 3, 2, 50.00),
(4, 2, 3, 41.00),
(4, 3, 3, 51.00);

-- Frango com Catupiry
INSERT INTO precos (pizza_id, tamanho_id, borda_id, preco) VALUES
(5, 1, 1, 29.00),
(5, 2, 1, 39.00),
(5, 3, 1, 49.00),
(5, 2, 2, 42.00),
(5, 3, 2, 52.00),
(5, 2, 3, 43.00),
(5, 3, 3, 53.00);

-- Doce de Leite com Coco (apenas borda tradicional, pois não se aplica borda recheada)
INSERT INTO precos (pizza_id, tamanho_id, borda_id, preco) VALUES
(6, 1, 1, 25.00),
(6, 2, 1, 35.00),
(6, 3, 1, 45.00);
