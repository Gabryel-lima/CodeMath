# Funções puras de um único return são expandidas por substituição.
# Recursão não: para isso precisaríamos de fix, como no while.
# sigma: x=3, y=4
def quadrado(a):
    return a * a

def dist2(a, b):
    return quadrado(a) + quadrado(b)

d = dist2(x, y)
maior = 0
if quadrado(x) > quadrado(y):
    maior = x
else:
    maior = y
