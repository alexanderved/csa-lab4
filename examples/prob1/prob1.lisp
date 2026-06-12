(defun reverse-number (n res)
    (if (= n 0)
        res
        (reverse-number (/ n 10)
                        (+ (* res 10) (% n 10)))))

(defun is-palindrome (n)
    (= n (reverse-number n 0)))

(defvar max 0)
(defun find-max-palindrome (i j)
    (if (< i 100)
        max
        (if (< j i)
            (find-max-palindrome (- i 1) 999)
            (let ((prod (* i j)))
                (if (is-palindrome prod)
                    (if (> prod max)
                        (setq max prod)))
                (if (> max (* i 999))
                    max
                    (find-max-palindrome i (- j 1)))))))

(output 2 (find-max-palindrome 999 999))