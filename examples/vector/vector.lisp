(defvar arr-len 5)

(defvar A (allocate-array 5))
(defvar B (allocate-array 5))
(defvar C (allocate-array 5))

(defvar i-A 0)
(defvar is-A-finished 0)

(defvar i-B 0)
(defvar is-B-finished 0)

(defun read-arrays ()
    (declare (interrupt 0))
    (if is-A-finished
        (if is-B-finished
            0
            (if (< i-B arr-len)
                (let ((el (input 0)))
                    (write-array-element B i-B el)
                    (setq i-B (+ i-B 1))
                    (if (= el 0)
                        (setq is-B-finished 1))
                    (if (= i-B arr-len)
                        (setq is-B-finished 1)))
                (setq is-B-finished 1)))
        (if (< i-A arr-len)
            (let ((el (input 0)))
                (write-array-element A i-A el)
                (setq i-A (+ i-A 1))
                (if (= el 0)
                    (setq is-A-finished 1)))
            (setq is-A-finished 1))))

(defun wait-arrays ()
    (if is-A-finished
        (if is-B-finished 0 (wait-arrays))
        (wait-arrays)))

(defun exec (i)
    (declare (optimize vector)) ; <-- Данная директива включает векторизацию функции
    (if (< i arr-len)
        (let ((a (read-array-element A i)) (b (read-array-element B i)))
            (if (> a b)
                (write-array-element C i (- a b))
                (write-array-element C i (- b a)))
            (exec (+ i 1)))))

(wait-arrays)
(exec 0)
(print-array C arr-len)