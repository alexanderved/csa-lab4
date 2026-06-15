; Функция с хвостовой рекурсией
(defun factorial-inner (n acc)
    (if n
        (factorial-inner (- n 1) (* acc n))
        acc))

(defun factorial (n)
    ; Вызов inline-функции
    (factorial-inner n 1))

(defvar a 8)
(defvar b 7)
(defvar string "demo")
(defvar arr (allocate-array 5))

; Печать строки
(print-string string)

; Примеры expression'ов
(output 2 a)
(output 2 (+ a b))
(output 2 (factorial a))
(output 2 (factorial b))
(output 2 (setq b 5))
(output 2 (write-array-element arr 1 6))

; if как expression
(output 2 (if (> a b) a b))

; let как expression
(output 2 (let ((c (* a b))) c))