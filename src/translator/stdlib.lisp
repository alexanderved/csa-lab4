(defun print-string-loop (s i)
    (let ((c (read-char s i)))
        (if c
            (let ()
                (output 1 c)
                (print-string-loop s (+ i 1)))
            i)))

(defun print-string (s)
    (print-string-loop s 0))

(defun print-array-loop (arr len i)
    (if (< i len)
        (let ((el (read-array-element arr i)))
            (output 2 el)
            (print-array-loop arr len (+ i 1)))
        i))

(defun print-array (arr len)
    (print-array-loop arr len 0))