# Fibonacci iterativo. A troca simultânea é o coração do algoritmo.
# sigma: n=10
a = 0
b = 1
i = 0
while i < n:
    a, b = b, a + b
    i = i + 1
fib = a
