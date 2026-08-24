# Este laço não termina para nenhum estado. Sua denotação é a função
# totalmente indefinida: fix(F) = bot. Note que a função constante
# f(s) = s também satisfaz F(f) = f -- ela é um ponto fixo, só não é
# o MENOR. Escolher o menor é o que impede a semântica de inventar
# um resultado para um programa que nunca produz nenhum.
# sigma: x=1
# esperado: diverge
while x > 0:
    x = x + 1
