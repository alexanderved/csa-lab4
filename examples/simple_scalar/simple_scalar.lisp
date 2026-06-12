(defvar arr-len 0)
(defvar arr-cap 32)
(defvar A (allocate-array 32))
(defvar is-finished 0)

(defun read-array ()
    (declare (interrupt 0))
    (if is-finished
        0
        (if (< arr-len arr-cap)
            (let ((el (input 0)))
                (if (= el 0)
                    (setq is-finished 1)
                    (let ()
                        (write-array-element A arr-len el)
                        (setq arr-len (+ arr-len 1)))))
            (setq is-finished 1))))

(defun wait-array ()
    (if is-finished 0 (wait-array)))

(defun invert-A (i)
    ; (declare (optimize vector))
    (if (< i arr-cap)
        (let ((el (read-array-element A i)))
            (write-array-element A i (- 0 el))
            (invert-A (+ i 1)))))

(wait-array)
(invert-A 0)
(print-array A arr-len)