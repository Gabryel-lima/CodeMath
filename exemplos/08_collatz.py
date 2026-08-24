# Collatz. Ninguém sabe provar que este laço termina para todo n > 0 --
# e é exatamente por isso que a denotação usa o MENOR ponto fixo:
# ela não precisa saber. Onde não termina, denota indefinido.
# sigma: n=27
passos = 0
while n != 1:
    if n % 2 == 0:
        n = n // 2
    else:
        n = 3 * n + 1
    passos = passos + 1
