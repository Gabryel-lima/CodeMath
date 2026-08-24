# Algoritmo de Euclides. Invariante: mdc(a, b) é constante ao longo do laço.
# sigma: a=48, b=18
# sigma: a=17, b=5
while b != 0:
    a, b = b, a % b
mdc = a
