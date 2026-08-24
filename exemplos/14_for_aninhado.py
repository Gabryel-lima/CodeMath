# Dois for aninhados: dois funcionais, cada um com seu estado ligado.
# sigma:
total = 0
for i in range(0, 4):
    for j in range(0, 4):
        if i != j:
            total = total + i * j
